"""提醒分组的数据库装配：把 ORM 行转成读侧聚合所需的成员并分组。

读写两侧共用：
- 提醒列表/详情（``app.risks.router``）用代表行展示、用组内全部行展开；
- 概览统计（``app.risks.dashboard``）用代表行计数，避免"列表 44 条、概览 70 条"。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.risks.grouping import (
    AlertGroupMember,
    event_region_keys,
    event_time_window,
    group_alert_members,
)
from app.risks.models import EventLocation, RiskAlert, RiskEvent, SupplierEventMatch
from app.risks.query_validity import current_alert_condition
from app.suppliers.models import Supplier

AlertRow = tuple[Any, Any, Any, Any]


def current_alert_rows(
    session: Session, *, now_utc: datetime, supplier_id: int | None = None
) -> list[AlertRow]:
    """当前有效提醒行 (alert, match, event, supplier)。"""
    filters = [current_alert_condition(now_utc)]
    if supplier_id is not None:
        filters.append(SupplierEventMatch.supplier_id == supplier_id)
    return list(
        session.execute(
            select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
            .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
            .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
            .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
            .where(*filters)
            .order_by(RiskAlert.id)
        ).all()
    )


def group_current_rows(
    session: Session, rows: Sequence[AlertRow]
) -> dict[int, list[AlertRow]]:
    """按读侧聚合规则分组，返回 alert_id → 同组全部行。"""
    if not rows:
        return {}
    event_ids = {event.id for _, _, event, _ in rows}
    locations = session.scalars(
        select(EventLocation).where(EventLocation.event_id.in_(event_ids))
    ).all()
    locations_by_event: dict[int, list[EventLocation]] = {}
    for location in locations:
        locations_by_event.setdefault(location.event_id, []).append(location)

    members: list[AlertGroupMember] = []
    rows_by_alert: dict[int, AlertRow] = {}
    for alert, match, event, supplier in rows:
        window = event_time_window(
            start_at=event.start_at,
            end_at=event.end_at,
            valid_until=event.valid_until,
            created_at=event.created_at,
            updated_at=event.updated_at,
        )
        if window is None:
            continue
        members.append(
            AlertGroupMember(
                alert_id=alert.id,
                supplier_id=supplier.id,
                event_type=event.event_type,
                event_id=event.id,
                level=alert.level,
                score=alert.score,
                updated_at=alert.updated_at,
                start_at=window[0],
                end_at=window[1],
                region_keys=event_region_keys(locations_by_event.get(event.id, [])),
            )
        )
        rows_by_alert[alert.id] = (alert, match, event, supplier)

    groups_by_alert: dict[int, list[AlertRow]] = {}
    for group in group_alert_members(members):
        group_rows = [rows_by_alert[member.alert_id] for member in group]
        for member in group:
            groups_by_alert[member.alert_id] = group_rows
    return groups_by_alert


def representative_rows(
    session: Session, rows: Sequence[AlertRow]
) -> tuple[list[AlertRow], dict[int, list[AlertRow]]]:
    """每组取一个代表行（等级最高、同级最近更新），返回 (代表行, alert_id → 组内行)。"""
    groups = group_current_rows(session, rows)
    level_rank = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    reps: list[AlertRow] = []
    seen: set[int] = set()
    for alert, match, event, supplier in rows:
        group = groups.get(alert.id)
        if group is None:
            reps.append((alert, match, event, supplier))
            continue
        key = id(group)
        if key in seen:
            continue
        seen.add(key)
        reps.append(
            min(
                group,
                key=lambda row: (
                    level_rank.get(row[0].level, len(level_rank)),
                    -row[0].updated_at.timestamp(),
                ),
            )
        )
    return reps, groups
