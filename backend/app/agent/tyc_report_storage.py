"""天眼查报告信号存储（计划 Todo 9）：周内幂等、跨周 until_superseded 原子替代。

从 ``supplier_tyc`` 拆出以控制模块规模；本模块只负责「已构造好的 TycRiskReport →
raw_signals」的持久化，不查远程、不改信源配置。

契约：
- ``external_id = tyc-<supplier_code>-<report.period_key>``：同一 ISO 周恒定，跨周变化；
- 指纹 = 固定字段 allowlist 的 canonical JSON（含 period_key，显式排除 generated_at
  等运行时字段）；模型未来新增字段不会悄然改变指纹，必须显式加入 allowlist；
- 同周先到先得：external_id 已存在即返回 ``duplicate``，不新增也不替代（即使业务
  字段变化），避免重复 external_id 与两条冲突画像；
- 跨周：新 external_id 经既有 supersession 机制（同键 advisory lock + active 唯一
  索引）原子替代旧 active；并发同周写入由同一把锁串行化，只保留一个 champion；
- 空报告（无任何维度条目）不落库并返回 ``empty``，由批次计入 empty；维度条目全部
  失败的非空报告仍如实落库（与 Todo 7 的部分报告契约一致）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agent.supplier_tyc import TYC_SOURCE_CODE, tyc_source
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
class TycReportWrite:
    """一次报告写入的判定结果；``external_id`` 同周恒定。"""

    external_id: str
    outcome: TycReportOutcome


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
) -> TycReportWrite:
    """写入一份周度报告信号；返回 external_id 与结果（created/duplicate/empty）。"""
    external_id = f"tyc-{supplier.supplier_code}-{report.period_key}"
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
        return TycReportWrite(external_id=external_id, outcome="duplicate")
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
                fingerprint=report_fingerprint(report),
                collected_at=report.generated_at,
                authority=LifecycleAuthority("adapter", TYC_SOURCE_CODE),
                anchor_fallback_source=TYC_SOURCE_CODE,
                raw_data=report.model_dump(mode="json"),
            )
        ],
    )
    return TycReportWrite(
        external_id=external_id,
        outcome="created" if created else "duplicate",
    )


def _lock_validity_key(session: Session, source_id: int, validity_key: str) -> None:
    """同键事务级 advisory lock：同周检查/写入与跨周替代共用一把锁串行化。"""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": advisory_key1(source_id), "k2": advisory_key2(validity_key)},
    )
