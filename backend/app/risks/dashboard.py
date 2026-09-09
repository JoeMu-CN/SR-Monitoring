"""总览汇总聚合：当前统计与期间新增的明确口径。

当前统计全部共享同一 as_of 的 current_alert_condition；期间新增统计
窗口内创建的全部提醒行（含已失效），两者命名与语义明确分离。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session

from app.config import get_retention_settings
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.query_validity import current_alert_condition
from app.risks.schemas import EventTypeCount, LevelCount, SourceDistributionItem
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

RECENT_ALERT_LIMIT: Final = 10
TODAY_LOOKBACK: Final = timedelta(days=1)
CURRENT_LEVELS: Final = ("P1", "P2", "P3", "P4")

RecentAlertRow = Row[tuple[RiskAlert, SupplierEventMatch, RiskEvent, Supplier]]


@dataclass(frozen=True, slots=True)
class DashboardAggregate:
    """单次 as_of 下的全部汇总值与最近提醒原始行。"""

    as_of: datetime
    window_start: datetime
    window_days: int
    level_counts: list[LevelCount]
    total_current: int
    today_new: int
    type_distribution: list[EventTypeCount]
    period_new_count: int
    supplier_total: int
    active_supplier_total: int
    source_distribution: list[SourceDistributionItem]
    retention_window_days: int
    history_may_be_partial: bool
    recent_rows: Sequence[RecentAlertRow]


def utc_now() -> datetime:
    """返回当前 UTC 时间；独立函数以便测试冻结时钟。"""
    return datetime.now(UTC)


def build_dashboard_aggregate(
    session: Session, *, days: int, now_utc: datetime | None = None
) -> DashboardAggregate:
    """在同一 as_of 下完成全部只读聚合；调用方不得中途提交写入。

    前置条件：days 已在边界解析为 7/30/90 之一（路由层以 422 拒绝非法值）。
    """
    as_of = now_utc if now_utc is not None else utc_now()
    window_start = as_of - timedelta(days=days)
    current_condition = current_alert_condition(as_of)

    level_rows = session.execute(
        select(RiskAlert.level, func.count())
        .where(current_condition)
        .group_by(RiskAlert.level)
    ).all()
    level_map = {level: count for level, count in level_rows}
    level_counts = [
        LevelCount(level=level, count=level_map.get(level, 0))
        for level in CURRENT_LEVELS
    ]
    total_current = sum(level_map.values())

    today_new = (
        session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(
                current_condition,
                RiskAlert.created_at >= as_of - TODAY_LOOKBACK,
                RiskAlert.created_at < as_of,
            )
        )
        or 0
    )

    type_rows = session.execute(
        select(RiskEvent.event_type, func.count())
        .join(SupplierEventMatch, SupplierEventMatch.event_id == RiskEvent.id)
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .where(current_condition)
        .group_by(RiskEvent.event_type)
        .order_by(func.count().desc())
    ).all()
    type_distribution = [
        EventTypeCount(event_type=event_type, count=count)
        for event_type, count in type_rows
    ]

    period_new_count = (
        session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(
                RiskAlert.created_at >= window_start,
                RiskAlert.created_at < as_of,
            )
        )
        or 0
    )

    supplier_total = session.scalar(select(func.count()).select_from(Supplier)) or 0
    active_supplier_total = (
        session.scalar(
            select(func.count()).select_from(Supplier).where(Supplier.enabled.is_(True))
        )
        or 0
    )

    source_rows = session.execute(
        select(
            DataSource.id,
            DataSource.code,
            DataSource.name,
            func.count(func.distinct(RiskAlert.id)),
        )
        .join(RawSignal, RawSignal.source_id == DataSource.id)
        .join(RiskEventSignal, RiskEventSignal.signal_id == RawSignal.id)
        .join(
            SupplierEventMatch,
            SupplierEventMatch.event_id == RiskEventSignal.event_id,
        )
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .where(current_condition)
        .group_by(DataSource.id, DataSource.code, DataSource.name)
        .order_by(func.count(func.distinct(RiskAlert.id)).desc(), DataSource.code)
    ).all()
    source_distribution = [
        SourceDistributionItem(source_id=source_id, code=code, name=name, count=count)
        for source_id, code, name, count in source_rows
    ]

    recent_rows = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(current_condition)
        .order_by(RiskAlert.updated_at.desc(), RiskAlert.id.desc())
        .limit(RECENT_ALERT_LIMIT)
    ).all()

    retention_window_days = get_retention_settings().event_days
    return DashboardAggregate(
        as_of=as_of,
        window_start=window_start,
        window_days=days,
        level_counts=level_counts,
        total_current=total_current,
        today_new=today_new,
        type_distribution=type_distribution,
        period_new_count=period_new_count,
        supplier_total=supplier_total,
        active_supplier_total=active_supplier_total,
        source_distribution=source_distribution,
        retention_window_days=retention_window_days,
        history_may_be_partial=days > retention_window_days,
        recent_rows=recent_rows,
    )
