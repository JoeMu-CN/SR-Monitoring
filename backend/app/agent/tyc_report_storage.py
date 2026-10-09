"""天眼查报告信号存储（计划 Todo 9）：周内幂等、跨周 until_superseded 原子替代。

从 ``supplier_tyc`` 拆出以控制模块规模；本模块只负责「已构造好的 TycRiskReport →
raw_signals」的持久化，不查远程、不改信源配置。

契约：
- 定时路径（``observation=None``）：``external_id = tyc-<supplier_code>-<period_key>``
  同一 ISO 周恒定，跨周变化；指纹 = 固定字段 allowlist 的 canonical JSON（含
  period_key，显式排除 generated_at 等运行时字段）；模型未来新增字段不会悄然改变
  指纹，必须显式加入 allowlist；同周先到先得，external_id 已存在即返回
  ``duplicate``，不新增也不替代；
- 手动实时路径（传入 ``ManualObservation``）：external_id 与指纹都追加显式观察
  token，使同周每次实时核查都成为独立证据版本、不被周内幂等吞掉；``validity_key``
  不变，因此 until_superseded 仍把最新观察原子替代旧 active；
- 手动路径额外返回真实 ``signal_id``（按唯一键 ``(source_id, fingerprint)`` 取回），
  供调用方继续对该信号做 AI 解析与规则判定；定时路径不需要该 id，故不额外查询；
- 空报告（无任何维度条目）不落库并返回 ``empty``，由批次计入 empty；维度条目全部
  失败的非空报告仍如实落库（与 Todo 7 的部分报告契约一致）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agent.supplier_tyc import TYC_SOURCE_CODE, tyc_source
from app.agent.tyc_analysis_context import PRIVATE_RAW_FIELD, TycAnalysisContext
from app.agent.tyc_report import TycRiskReport, render_key_summary
from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    persist_signal_ingestions,
)
from app.signals.models import RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.supersession import advisory_key1, advisory_key2
from app.signals.validity import LifecycleAction, ValidityProfile
from app.suppliers.models import Supplier

TycReportOutcome = Literal["created", "duplicate", "empty"]


@dataclass(frozen=True, slots=True)
class ManualObservation:
    """一次手动实时核查的观察身份。

    ``observed_at`` 既是权威时间（决定 until_superseded 替代胜负），也派生唯一
    token，使同周的多次实时核查各自成为独立证据版本；同一时刻重放同一次核查得到
    相同 token，因而仍然幂等。
    """

    observed_at: datetime

    @property
    def token(self) -> str:
        return self.observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")


@dataclass(frozen=True, slots=True)
class TycReportWrite:
    """一次报告写入的判定结果；``external_id`` 同周恒定（手动路径含观察 token）。"""

    external_id: str
    outcome: TycReportOutcome
    signal_id: int | None = None


def report_fingerprint(report: TycRiskReport) -> str:
    """固定字段 allowlist 的 canonical JSON 指纹；generated_at 等运行时字段除外。"""
    payload: dict[str, object] = {
        "report_kind": report.report_kind,
        "company_name": report.company_name,
        "supplier_code": report.supplier_code,
        "credit_code": report.credit_code,
        "reg_status": report.reg_status,
        "period_key": report.period_key,
        "dimensions": [
            {
                "key": finding.key,
                "name": finding.name,
                "status": finding.status,
                "risk_level": finding.risk_level,
                "hit": finding.hit,
                "summary": finding.summary,
                "evidence_refs": finding.evidence_refs,
                "raw_ref": finding.raw_ref,
            }
            for finding in report.dimensions
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_tyc_report_signal(
    session: Session,
    *,
    supplier: Supplier,
    report: TycRiskReport,
    observation: ManualObservation | None = None,
    analysis_context: TycAnalysisContext | None = None,
) -> TycReportWrite:
    """写入一份报告信号；返回 external_id、真实 signal_id 与结果。

    ``observation`` 为 None 时是定时周度写入（同周幂等、跨周原子替代）；非 None 时
    是手动实时观察，external_id 与指纹都带观察 token，因此同周重复实时核查会
    产生新证据行而不被周内去重吞掉。

    ``analysis_context`` 是可选的私有 LLM 上下文：只作为 ``raw_data`` 的附加键落库，
    不参与指纹（``report_fingerprint`` 仍只覆盖原报告字段），因此补上下文既不会
    改变去重口径，也不会让既有报告 API 报告字段变化；缺省（None）保持历史行为。
    """
    external_id = f"tyc-{supplier.supplier_code}-{report.period_key}"
    if observation is not None:
        external_id = f"{external_id}-{observation.token}"
    if not report.dimensions:
        return TycReportWrite(external_id=external_id, outcome="empty")
    source = tyc_source(session)
    validity_key = f"tyc:{supplier.supplier_code}"
    _lock_validity_key(session, source.id, validity_key)
    duplicate = session.scalar(
        select(RawSignal.id).where(
            RawSignal.source_id == source.id,
            RawSignal.external_id == external_id,
        )
    )
    if duplicate is not None:
        return TycReportWrite(
            external_id=external_id, outcome="duplicate", signal_id=duplicate
        )
    base_fingerprint = report_fingerprint(report)
    fingerprint = (
        base_fingerprint
        if observation is None
        else _observation_fingerprint(base_fingerprint, observation)
    )
    raw_data = report.model_dump(mode="json")
    if analysis_context is not None:
        # 私有上下文只作为附加键落库：根报告既有字段保持不变，指纹口径也不含它。
        raw_data[PRIVATE_RAW_FIELD] = analysis_context.model_dump(mode="json")
    created = persist_signal_ingestions(
        session,
        [
            SignalIngestion(
                source=source,
                signal=ManualSignalInput(
                    external_id=external_id,
                    title=f"天眼查多维度核查：{supplier.legal_name}",
                    content=render_key_summary(report),
                    validity_profile=ValidityProfile.ADVERSE_REGISTRY,
                    validity_key=validity_key,
                    lifecycle_action=LifecycleAction.ASSERT,
                ),
                fingerprint=fingerprint,
                collected_at=report.generated_at,
                authority=LifecycleAuthority("adapter", TYC_SOURCE_CODE),
                anchor_fallback_source=TYC_SOURCE_CODE,
                raw_data=raw_data,
            )
        ],
    )
    # 观察身份参与指纹后无法从写入返回值取 id；按唯一键 (source_id, fingerprint)
    # 取回本行主键，供手动路径继续对该信号做 AI 解析与规则判定。
    return TycReportWrite(
        external_id=external_id,
        outcome="created" if created else "duplicate",
        signal_id=session.scalar(
            select(RawSignal.id).where(
                RawSignal.source_id == source.id,
                RawSignal.fingerprint == fingerprint,
            )
        ),
    )


def _observation_fingerprint(
    base_fingerprint: str, observation: ManualObservation
) -> str:
    """把观察 token 混入报告指纹：同报告、不同观察时刻即为不同证据版本。"""
    payload = json.dumps(
        {"report": base_fingerprint, "observation": observation.token},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _lock_validity_key(session: Session, source_id: int, validity_key: str) -> None:
    """同键事务级 advisory lock：同周检查/写入与跨周替代共用一把锁串行化。"""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": advisory_key1(source_id), "k2": advisory_key2(validity_key)},
    )
