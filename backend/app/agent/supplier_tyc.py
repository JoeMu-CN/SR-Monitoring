"""供应商-天眼查信号桥接：查询/落库已采集的天眼查核查信号。

设计原则：
- 手动风险查询助手查「清单内供应商」时只读已入库最新天眼查信号（不消耗额度）；
  清单外企业仍走 VerifyCompanyTool 实时 MCP 路径。
- 定时批量核查 job 调用 ``write_tyc_signal`` 把核查结果写入 raw_signals，
  复用 collect_source 的指纹去重语义（on_conflict_do_nothing）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    persist_signal_ingestions,
)
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.validity import ValidityProfile
from app.suppliers.models import Supplier

TYC_SOURCE_CODE = "tianyancha"

_LAST_N = 1  # 返回每个供应商最近一条天眼查信号

_MAX_RESULT_CANDIDATES = 3  # 返回给助手/前端的候选企业上限


@dataclass(frozen=True, slots=True)
class _ParsedTycContent:
    """从信号正文（_format_tyc_content 生成）回退解析出的字段。"""

    company_name: str | None
    credit_code: str | None
    reg_status: str | None
    candidates: list[dict[str, object]]


def latest_tyc_signals_for_supplier(
    session: Session,
    supplier: Supplier,
    *,
    limit: int = _LAST_N,
) -> list[RawSignal]:
    """返回该供应商已入库的最新天眼查信号（按 collected_at 倒序）。"""
    prefix = f"tyc-{supplier.supplier_code}-"
    return list(
        session.scalars(
            select(RawSignal)
            .where(
                RawSignal.source_id == _tyc_source_id(session),
                RawSignal.external_id.like(f"{prefix}%"),
            )
            .order_by(RawSignal.collected_at.desc())
            .limit(limit)
        )
    )


def upsert_supplier_tyc_signal(
    session: Session,
    *,
    supplier: Supplier,
    title: str,
    content: str,
    raw_payload: dict[str, object],
    url: str | None = None,
) -> tuple[str, bool]:
    """写入一条天眼查信号，返回 (external_id, created)。

    external_id 带 supplier_code 前缀，便于按供应商回溯与指纹去重。
    """
    collected_at = datetime.now(UTC)
    external_id = f"tyc-{supplier.supplier_code}-{int(collected_at.timestamp() * 1000)}"
    fingerprint = _fingerprint(title, content, url)
    source = _tyc_source(session)
    existing = session.scalar(
        select(RawSignal).where(
            RawSignal.source_id == source.id,
            RawSignal.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        return existing.external_id or external_id, False
    signal_url: HttpUrl | None = HttpUrl(url) if url else None
    signal = ManualSignalInput(
        external_id=external_id,
        title=title,
        content=content,
        url=signal_url,
        validity_profile=ValidityProfile.ADVERSE_REGISTRY,
    )
    created = persist_signal_ingestions(
        session,
        [
            SignalIngestion(
                source=source,
                signal=signal,
                fingerprint=fingerprint,
                collected_at=collected_at,
                authority=LifecycleAuthority("adapter", TYC_SOURCE_CODE),
                anchor_fallback_source=TYC_SOURCE_CODE,
                raw_data=raw_payload,
            )
        ],
    )
    session.flush()
    return external_id, bool(created)


def _tyc_source_id(session: Session) -> int:
    return _tyc_source(session).id


def _tyc_source(session: Session) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == TYC_SOURCE_CODE))
    if source is None:
        raise RuntimeError("天眼查信息源未配置")
    return source


def _fingerprint(title: str, content: str, url: str | None) -> str:
    canonical = json.dumps(
        {"title": title, "content": content, "url": url},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def format_tyc_signal_result(signal: RawSignal) -> dict[str, object]:
    """把已入库天眼查信号转成助手可读的返回结构。

    结构化字段（企业名称/统一社会信用代码/登记状态/候选）优先取 ``raw_data``
    （verify() 原始载荷）；旧信号或载荷缺失时回退解析 ``content``
    （_format_tyc_content 生成的「企业：…；统一社会信用代码：…；登记状态：…」文本）。
    确实缺失的字段返回 None，展示兜底由前端负责，后端不输出「未披露」。
    """
    raw: dict[str, object] = signal.raw_data if isinstance(signal.raw_data, dict) else {}
    parsed = _parse_tyc_content(signal.content)
    candidates = _normalize_candidates(raw.get("candidates")) or parsed.candidates
    return {
        "status": "success",
        "source": "database",
        "title": signal.title,
        "content": signal.content,
        "url": signal.url,
        "collected_at": signal.collected_at.isoformat(),
        "external_id": signal.external_id,
        "company_name": _text_or_none(raw.get("company_name")) or parsed.company_name,
        "credit_code": _text_or_none(raw.get("credit_code")) or parsed.credit_code,
        "reg_status": _text_or_none(raw.get("reg_status")) or parsed.reg_status,
        "candidates": candidates,
    }


def _parse_tyc_content(content: object) -> _ParsedTycContent:
    """回退解析 _format_tyc_content 生成的正文：「；」分段、首个「：」分列。"""
    company_name: str | None = None
    credit_code: str | None = None
    reg_status: str | None = None
    candidates: list[dict[str, object]] = []
    if isinstance(content, str):
        for segment in content.split("；"):
            label, separator, value = segment.partition("：")
            text = value.strip()
            if not separator or not text:
                continue
            label = label.strip()
            if label == "企业" and company_name is None:
                company_name = text
            elif label == "统一社会信用代码" and credit_code is None:
                credit_code = text
            elif label == "登记状态" and reg_status is None:
                reg_status = text
            elif label.startswith("候选") and len(candidates) < _MAX_RESULT_CANDIDATES:
                candidates.append({"name": text, "credit_code": None, "reg_status": None})
    return _ParsedTycContent(company_name, credit_code, reg_status, candidates)


def _normalize_candidates(value: object) -> list[dict[str, object]]:
    """归一化 raw_data.candidates：最多 3 条，跳过非字典/无名条目。"""
    if not isinstance(value, list):
        return []
    candidates: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _text_or_none(item.get("name"))
        if name is None:
            continue
        candidates.append(
            {
                "name": name,
                "credit_code": _text_or_none(item.get("credit_code")),
                "reg_status": _text_or_none(item.get("reg_status")),
            }
        )
        if len(candidates) >= _MAX_RESULT_CANDIDATES:
            break
    return candidates


def _text_or_none(value: object) -> str | None:
    """非字符串或空白文本归一为 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
