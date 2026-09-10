"""数据保留清理（技术方案 3.3）。

- 原始风险信号和 AI 分析结果默认保留 90 天（可配置 RETENTION_SIGNAL_DAYS）。
- 风险事件和风险提醒保留至失效后 90 天（可配置 RETENTION_EVENT_DAYS）。
- 采集运行记录默认保留 30 天（可配置 RETENTION_RUN_DAYS）。

证据闭包与删除顺序：任何尚保留提醒（current 有效、legacy 保守保留、expired
未过 90 天窗口）所连接的 match → event → RiskEventSignal → RawSignal 及该信号
的全部分析均受保护；删除固定按 alert → match → event → signal/analysis →
collection run 顺序，匹配仅在无任何剩余提醒后删除，事件仅在无剩余匹配且不承载
受保护引用后删除，不以 ON DELETE CASCADE 代替业务判断。

并发与原子性：绑定独立 Engine 时，清理在专用 SERIALIZABLE 事务内按固定表/ID
顺序锁定候选并重新校验引用；序列化冲突（sqlstate 40001）最多重试 3 次，每次
重新取得时钟与保护集合，最终失败整轮回滚，绝无局部提交。绑定外部连接（测试
savepoint 夹具）时沿用调用方事务边界，只 flush 不提交。单轮只使用一个冻结时钟
（可注入 ``now_utc``）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import CursorResult, Engine, delete, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.config import RetentionSettings
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.scheduler.retention_queries import (
    classify_expired_alerts,
    protected_signal_ids,
)
from app.signals.models import CollectionRun, RawSignal

SERIALIZABLE_RETRY_LIMIT: Final = 3
SIGNAL_BATCH_LIMIT: Final = 1000
ORPHAN_EVENT_DAYS: Final = 7
ORPHAN_EVENT_LIMIT: Final = 500


@dataclass
class CleanupResult:
    expired_alerts: int = 0
    deleted_events: int = 0
    deleted_signals: int = 0
    deleted_analysis: int = 0
    deleted_runs: int = 0
    protected_skipped: int = 0
    anomaly_anchor_skipped: int = 0


def cleanup_retention(
    session: Session,
    settings: RetentionSettings | None = None,
    *,
    now_utc: datetime | None = None,
) -> CleanupResult:
    """运行一轮保留清理并返回删除与保护计数。

    ``now_utc`` 可注入以冻结整轮时钟；缺省使用实时钟。
    """
    if settings is None:
        from app.config import get_retention_settings

        settings = get_retention_settings()
    assert settings is not None
    clock = _clock(now_utc)
    bind = session.get_bind()
    if isinstance(bind, Engine):
        return _run_in_serializable_transaction(bind, settings, clock)
    return _run_cleanup(session, settings, clock())


def _clock(now_utc: datetime | None) -> Callable[[], datetime]:
    if now_utc is not None:
        return lambda: now_utc
    return lambda: datetime.now(UTC)


def _run_in_serializable_transaction(
    bind: Engine, settings: RetentionSettings, clock: Callable[[], datetime]
) -> CleanupResult:
    last_conflict: DBAPIError | None = None
    for attempt in range(1, SERIALIZABLE_RETRY_LIMIT + 1):
        connection = bind.connect().execution_options(isolation_level="SERIALIZABLE")
        transaction = connection.begin()
        work = Session(bind=connection, expire_on_commit=False)
        try:
            result = _run_cleanup(work, settings, clock())
            work.commit()
            transaction.commit()
        except DBAPIError as exc:
            work.rollback()
            transaction.rollback()
            if _is_serialization_failure(exc) and attempt < SERIALIZABLE_RETRY_LIMIT:
                last_conflict = exc
                continue
            raise
        else:
            return result
        finally:
            work.close()
            connection.close()
    assert last_conflict is not None
    raise last_conflict


def _is_serialization_failure(exc: DBAPIError) -> bool:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return isinstance(sqlstate, str) and sqlstate == "40001"


def _run_cleanup(
    session: Session, settings: RetentionSettings, now: datetime
) -> CleanupResult:
    result = CleanupResult()
    classification = classify_expired_alerts(
        session, now_utc=now, event_days=settings.event_days
    )
    result.anomaly_anchor_skipped = classification.anomaly_anchor_count
    protected = protected_signal_ids(
        session, now_utc=now, event_days=settings.event_days
    )

    # 1) 删到期提醒 → 锁定匹配 → 删无任何剩余提醒的匹配 → 锁定事件 →
    #    删无剩余匹配且无受保护引用的事件
    affected_match_ids: set[int] = set()
    for alert in classification.deletable:
        affected_match_ids.add(alert.match_id)
        session.delete(alert)
        session.flush()
        result.expired_alerts += 1

    locked_matches = list(
        session.scalars(
            select(SupplierEventMatch)
            .where(SupplierEventMatch.id.in_(affected_match_ids))
            .order_by(SupplierEventMatch.id)
            .with_for_update()
        )
    )
    candidate_event_ids: set[int] = set()
    for match in locked_matches:
        remaining_alerts = session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(RiskAlert.match_id == match.id)
        )
        if remaining_alerts:
            continue
        candidate_event_ids.add(match.event_id)
        session.delete(match)
        session.flush()

    locked_events = list(
        session.scalars(
            select(RiskEvent)
            .where(RiskEvent.id.in_(candidate_event_ids))
            .order_by(RiskEvent.id)
            .with_for_update()
        )
    )
    for event in locked_events:
        _delete_event_if_unreferenced(session, event.id, protected, result)

    # 孤儿事件兜底：确无 match 且不承载受保护引用、超过 7 天的早期事件
    orphan_cutoff = now - timedelta(days=ORPHAN_EVENT_DAYS)
    orphan_events = list(
        session.scalars(
            select(RiskEvent)
            .where(
                RiskEvent.created_at < orphan_cutoff,
                ~RiskEvent.id.in_(select(SupplierEventMatch.event_id).distinct()),
            )
            .order_by(RiskEvent.id)
            .with_for_update()
            .limit(ORPHAN_EVENT_LIMIT)
        )
    )
    for event in orphan_events:
        _delete_event_if_unreferenced(session, event.id, protected, result)

    # 2) 信号与 AI 分析：超龄且不在受保护闭包才删；分析删除复用同一保护集合，
    #    保守保留受保护信号的全部分析版本
    signal_cutoff = now - timedelta(days=settings.signal_days)
    old_signals = list(
        session.scalars(
            select(RawSignal)
            .where(RawSignal.collected_at < signal_cutoff)
            .order_by(RawSignal.id)
            .with_for_update()
            .limit(SIGNAL_BATCH_LIMIT)
        )
    )
    for signal in old_signals:
        if signal.id in protected:
            result.protected_skipped += 1
            continue
        session.delete(signal)
        result.deleted_signals += 1
    session.flush()

    analysis_deleted = session.execute(
        delete(AIAnalysisRecord).where(
            AIAnalysisRecord.started_at < signal_cutoff,
            AIAnalysisRecord.signal_id.not_in(protected),
        )
    )
    result.deleted_analysis = _rowcount(analysis_deleted)

    # 3) 采集运行记录保留 run_days 天
    run_cutoff = now - timedelta(days=settings.run_days)
    runs_deleted = session.execute(
        delete(CollectionRun).where(CollectionRun.started_at < run_cutoff)
    )
    result.deleted_runs = _rowcount(runs_deleted)
    session.flush()
    return result


def _delete_event_if_unreferenced(
    session: Session,
    event_id: int,
    protected: frozenset[int],
    result: CleanupResult,
) -> None:
    """事件仅在无剩余匹配且不承载受保护信号引用后删除。"""
    event = session.get(RiskEvent, event_id)
    if event is None:
        return
    remaining_matches = session.scalar(
        select(func.count())
        .select_from(SupplierEventMatch)
        .where(SupplierEventMatch.event_id == event_id)
    )
    if remaining_matches:
        return
    carries_protected_reference = session.scalar(
        select(RiskEventSignal.signal_id)
        .where(
            RiskEventSignal.event_id == event_id,
            RiskEventSignal.signal_id.in_(protected),
        )
        .limit(1)
    )
    if carries_protected_reference is not None:
        return
    session.delete(event)
    session.flush()
    result.deleted_events += 1


def _rowcount(result: object) -> int:
    """从 SQLAlchemy 执行结果取受影响行数（兼容 Result/CursorResult）。"""
    if isinstance(result, CursorResult):
        rowcount = result.rowcount
        return rowcount if rowcount is not None else 0
    return 0
