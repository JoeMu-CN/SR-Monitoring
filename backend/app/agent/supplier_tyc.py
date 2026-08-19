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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.models import RawSignal
from app.suppliers.models import Supplier

TYC_SOURCE_CODE = "tianyancha"

_LAST_N = 1  # 返回每个供应商最近一条天眼查信号


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
    import time

    external_id = f"tyc-{supplier.supplier_code}-{int(time.time() * 1000)}"
    fingerprint = _fingerprint(title, content, url)
    existing = session.scalar(
        select(RawSignal).where(
            RawSignal.source_id == _tyc_source_id(session),
            RawSignal.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        return existing.external_id or external_id, False
    session.add(
        RawSignal(
            source_id=_tyc_source_id(session),
            external_id=external_id,
            title=title,
            content=content,
            url=url,
            published_at=None,
            fingerprint=fingerprint,
            raw_data=raw_payload,
        )
    )
    session.flush()
    return external_id, True


def _tyc_source_id(session: Session) -> int:
    from app.signals.models import DataSource

    source = session.scalar(
        select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
    )
    if source is None:
        raise RuntimeError("天眼查数据源未配置")
    return source.id


def _fingerprint(title: str, content: str, url: str | None) -> str:
    canonical = json.dumps(
        {"title": title, "content": content, "url": url},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def format_tyc_signal_result(signal: RawSignal) -> dict[str, object]:
    """把已入库天眼查信号转成助手可读的返回结构。"""
    return {
        "status": "success",
        "source": "database",
        "title": signal.title,
        "content": signal.content,
        "url": signal.url,
        "collected_at": signal.collected_at.isoformat(),
        "external_id": signal.external_id,
    }
