"""数据保留清理（技术方案 3.3）。

- 原始风险信号和 AI 分析结果默认保留 90 天（可配置 RETENTION_SIGNAL_DAYS）。
- 风险事件和风险提醒保留至失效后 90 天（可配置 RETENTION_EVENT_DAYS）。
- 采集运行记录默认保留 30 天（可配置 RETENTION_RUN_DAYS）。

删除顺序注意外键：raw_signals 被 ai_analysis_records 和 risk_event_signals 引用，
先删事件相关（risk_alerts -> supplier_event_matches -> risk_events -> risk_event_signals），
再删信号（raw_signals 级联 ai_analysis_records）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.config import RetentionSettings
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    SupplierEventMatch,
)
from app.signals.models import CollectionRun, RawSignal


@dataclass
class CleanupResult:
    expired_alerts: int = 0
    deleted_events: int = 0
    deleted_signals: int = 0
    deleted_analysis: int = 0
    deleted_runs: int = 0


def cleanup_retention(
    session: Session, settings: RetentionSettings | None = None
) -> CleanupResult:
    if settings is None:
        from app.config import get_retention_settings

        settings = get_retention_settings()
    assert settings is not None
    now = datetime.now(UTC)
    result = CleanupResult()

    # 1) 已失效提醒保留至失效后 event_days 天
    alert_cutoff = now - timedelta(days=settings.event_days)
    alert_rows = list(
        session.scalars(
            select(RiskAlert).where(
                RiskAlert.status == "expired",
                RiskAlert.expires_at.is_not(None),
                RiskAlert.expires_at < alert_cutoff,
            )
        )
    )
    for alert in alert_rows:
        match = session.get(SupplierEventMatch, alert.match_id)
        if match is None:
            session.delete(alert)
            session.flush()
            result.expired_alerts += 1
            continue
        event_id = match.event_id
        session.delete(alert)
        session.flush()
        session.delete(match)
        session.flush()
        result.expired_alerts += 1
        # 事件若无其他 match/alert 且已过期关联，删除事件及其明细
        remaining = session.scalar(
            select(SupplierEventMatch.id).where(
                SupplierEventMatch.event_id == event_id
            )
        )
        event = session.get(RiskEvent, event_id)
        if remaining is None and event is not None:
            session.delete(event)
            session.flush()
            result.deleted_events += 1
    # 清理孤儿事件（无任何 match 且已超过 7 天的早期事件，防御性兜底）
    orphan_cutoff = now - timedelta(days=7)
    orphan_events = list(
        session.scalars(
            select(RiskEvent)
            .where(
                RiskEvent.created_at < orphan_cutoff,
                ~RiskEvent.id.in_(
                    select(SupplierEventMatch.event_id).distinct()
                ),
            )
            .limit(500)
        )
    )
    for event in orphan_events:
        session.delete(event)
    result.deleted_events += len(orphan_events)

    # 2) 信号与 AI 分析结果保留 signal_days 天（删除 raw_signals 级联 ai_analysis_records）
    signal_cutoff = now - timedelta(days=settings.signal_days)
    old_signals = list(
        session.scalars(
            select(RawSignal).where(RawSignal.collected_at < signal_cutoff).limit(1000)
        )
    )
    for signal in old_signals:
        session.delete(signal)
    result.deleted_signals += len(old_signals)
    analysis_deleted = session.execute(
        delete(AIAnalysisRecord).where(AIAnalysisRecord.started_at < signal_cutoff)
    )
    result.deleted_analysis = _rowcount(analysis_deleted)

    # 3) 采集运行记录保留 run_days 天
    run_cutoff = now - timedelta(days=settings.run_days)
    runs_deleted = session.execute(
        delete(CollectionRun).where(CollectionRun.started_at < run_cutoff)
    )
    result.deleted_runs = _rowcount(runs_deleted)

    session.commit()
    return result


def _rowcount(result: object) -> int:
    """从 SQLAlchemy 执行结果取受影响行数（兼容 Result/CursorResult）。"""
    if isinstance(result, CursorResult):
        rowcount = result.rowcount
        return rowcount if rowcount is not None else 0
    return 0
