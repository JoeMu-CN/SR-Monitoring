"""风险事件簇：事件级（去重 RiskEvent）读侧聚合。

背景：提醒列表按「同供应商 + 同区域 + 时间重叠 + 同事件类型」合并成风险组，但一个
事件关联多个供应商时仍会重复出现 N 次；「按事件」视图需要先把事件本身聚合起来。

本模块只做读侧聚合，不改表、不迁移数据：

- 先按 ``event_id`` 去重（同一事件的多条匹配/提醒只保留一个事件成员）；
- 按 ``event_type`` 分桶，地区键相交且时间窗重叠才合并；缺地区或时间窗无法判定
  时保守地各自成簇；
- 地区键与时间窗直接复用 :mod:`app.risks.grouping` 的 ``event_region_keys`` /
  ``event_time_window`` / ``should_merge_event``，不复制规则；
- 实时判活统一走 :func:`app.risks.query_validity.live_alert_ids`（内部即
  ``current_alert_condition``），不按 ``status`` 字段二次推断；
- 簇状态：所有成员「全部提醒有效」为 ``active``；全部「没有任何有效提醒」为
  ``expired``；其余（部分成员/部分提醒失效）为 ``partially_expired``。

``state=expired`` 的口径：事件至少有一条失效提醒（``status='expired'`` 或
``expires_at <= now()``，由 SQL 判活谓词统一给出）。因此一个「旧版本提醒被新版本
替代」但仍有效的事件会以 ``partially_expired`` 出现在已失效视图，成员给出
``expired_reason='version_superseded'``；这正对应前端的「被新版本替代」提示。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.risks.grouping import (
    event_region_keys,
    event_time_window,
    should_merge_event,
)
from app.risks.models import (
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.query_validity import live_alert_ids
from app.risks.schemas import (
    LevelCount,
    RiskEventClusterMemberRead,
    RiskEventClusterRead,
)
from app.signals.models import DataSource, RawSignal

LEVEL_ORDER: Final[dict[str, int]] = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
STATE_CURRENT: Final = "current"
STATE_EXPIRED: Final = "expired"
STATUS_ACTIVE: Final = "active"
STATUS_PARTIALLY_EXPIRED: Final = "partially_expired"
STATUS_EXPIRED: Final = "expired"
REASON_EVENT_EXPIRED: Final = "event_expired"
REASON_ALERT_EXPIRED: Final = "alert_expired"
REASON_VERSION_SUPERSEDED: Final = "version_superseded"
FALLBACK_SOURCE_CODE: Final = ""
FALLBACK_SOURCE_TITLE: Final = "信号源已失效"
UNLOCATED_REGION_LABEL: Final = "未标注地区"
_FAR_FUTURE: Final = datetime.max.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class AlertSnapshot:
    """簇内事件的一条提醒快照；``is_live`` 来自 SQL 判活谓词。"""

    alert_id: int
    match_id: int
    supplier_id: int
    level: str
    score: int
    expires_at: datetime | None
    is_live: bool


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    """簇内一个去重事件的全部展示事实。"""

    event_id: int
    event_type: str
    summary: str
    validity_state: str
    start_at: datetime | None
    created_at: datetime
    window: tuple[datetime, datetime] | None
    region_keys: frozenset[str]
    source_code: str
    source_title: str
    published_at: datetime | None
    alerts: tuple[AlertSnapshot, ...]


@dataclass(slots=True)
class _EventAccumulator:
    """装配阶段的累加器：同一事件的多条提醒行在此合并。"""

    event: RiskEvent
    window: tuple[datetime, datetime] | None
    region_keys: frozenset[str]
    source_code: str
    source_title: str
    published_at: datetime | None
    alerts: list[AlertSnapshot] = field(default_factory=list)


def _load_locations(
    session: Session, event_ids: Sequence[int]
) -> dict[int, list[EventLocation]]:
    locations = session.scalars(
        select(EventLocation)
        .where(EventLocation.event_id.in_(list(event_ids)))
        .order_by(EventLocation.id)
    ).all()
    by_event: dict[int, list[EventLocation]] = {}
    for location in locations:
        by_event.setdefault(location.event_id, []).append(location)
    return by_event


def _load_latest_sources(
    session: Session, event_ids: Sequence[int]
) -> dict[int, tuple[str, str, datetime | None]]:
    """每个事件最新的信号来源：``published_at desc nullslast, id desc``。"""
    rows = session.execute(
        select(
            RiskEventSignal.event_id,
            DataSource.code,
            DataSource.name,
            RawSignal.published_at,
        )
        .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
        .join(DataSource, RawSignal.source_id == DataSource.id)
        .where(RiskEventSignal.event_id.in_(list(event_ids)))
        .order_by(
            RiskEventSignal.event_id,
            RawSignal.published_at.desc().nullslast(),
            RawSignal.id.desc(),
        )
    ).all()
    latest: dict[int, tuple[str, str, datetime | None]] = {}
    for event_id, code, name, published_at in rows:
        latest.setdefault(event_id, (code, name, published_at))
    return latest


def load_event_snapshots(
    session: Session,
    *,
    now_utc: datetime,
    event_ids: Sequence[int] | None = None,
) -> list[EventSnapshot]:
    """装载事件快照（含全部提醒），按同一 ``now_utc`` 固化判活结果。"""
    statement = (
        select(RiskEvent, RiskAlert, SupplierEventMatch)
        .join(SupplierEventMatch, SupplierEventMatch.event_id == RiskEvent.id)
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .order_by(RiskEvent.id, RiskAlert.id)
    )
    if event_ids is not None:
        statement = statement.where(RiskEvent.id.in_(list(event_ids) or [0]))
    rows = session.execute(statement).all()
    if not rows:
        return []

    live = live_alert_ids(
        session, now_utc=now_utc, alert_ids=[alert.id for _event, alert, _match in rows]
    )
    involved = sorted({event.id for event, _alert, _match in rows})
    locations_by_event = _load_locations(session, involved)
    sources_by_event = _load_latest_sources(session, involved)

    accumulators: dict[int, _EventAccumulator] = {}
    for event, alert, match in rows:
        accumulator = accumulators.get(event.id)
        if accumulator is None:
            source_code, source_title, published_at = sources_by_event.get(
                event.id, (FALLBACK_SOURCE_CODE, FALLBACK_SOURCE_TITLE, None)
            )
            accumulator = _EventAccumulator(
                event=event,
                window=event_time_window(
                    start_at=event.start_at,
                    end_at=event.end_at,
                    valid_until=event.valid_until,
                    created_at=event.created_at,
                    updated_at=event.updated_at,
                ),
                region_keys=event_region_keys(
                    locations_by_event.get(event.id, [])
                ),
                source_code=source_code,
                source_title=source_title,
                published_at=published_at,
            )
            accumulators[event.id] = accumulator
        accumulator.alerts.append(
            AlertSnapshot(
                alert_id=alert.id,
                match_id=match.id,
                supplier_id=match.supplier_id,
                level=alert.level,
                score=alert.score,
                expires_at=alert.expires_at,
                is_live=alert.id in live,
            )
        )

    return [
        EventSnapshot(
            event_id=accumulator.event.id,
            event_type=accumulator.event.event_type,
            summary=accumulator.event.summary,
            validity_state=accumulator.event.validity_state,
            start_at=accumulator.event.start_at,
            created_at=accumulator.event.created_at,
            window=accumulator.window,
            region_keys=accumulator.region_keys,
            source_code=accumulator.source_code,
            source_title=accumulator.source_title,
            published_at=accumulator.published_at,
            alerts=tuple(accumulator.alerts),
        )
        for accumulator in accumulators.values()
    ]


def _belongs_to_state(event: EventSnapshot, state: str) -> bool:
    """current=至少一条实时有效提醒；expired=至少一条失效提醒。

    已失效视图按「失效提醒行」取事件（``status='expired'`` 或 ``expires_at`` 已过，
    统一由 SQL 判活谓词给出），因此旧版本被新版本取代、但事件仍有当前有效提醒的
    情况也会出现在已失效视图，并以 ``partially_expired`` + ``version_superseded``
    说明原因，而不是悄悄消失。
    """
    if state == STATE_CURRENT:
        return any(alert.is_live for alert in event.alerts)
    return any(not alert.is_live for alert in event.alerts)


def _find(parents: list[int], index: int) -> int:
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def group_events(events: Sequence[EventSnapshot]) -> list[list[EventSnapshot]]:
    """并查集分组：同类型 + 地区相交 + 时间窗重叠，保持输入顺序稳定。"""
    parents = list(range(len(events)))
    for left in range(len(events)):
        for right in range(left + 1, len(events)):
            if should_merge_event(
                left_event_type=events[left].event_type,
                left_region_keys=events[left].region_keys,
                left_window=events[left].window,
                right_event_type=events[right].event_type,
                right_region_keys=events[right].region_keys,
                right_window=events[right].window,
            ):
                root_left, root_right = _find(parents, left), _find(parents, right)
                if root_left != root_right:
                    parents[root_right] = root_left

    collected: dict[int, list[EventSnapshot]] = {}
    for index, event in enumerate(events):
        collected.setdefault(_find(parents, index), []).append(event)
    return list(collected.values())


def _alert_basis(event: EventSnapshot) -> tuple[AlertSnapshot, ...]:
    """成员的有效基准提醒：优先当前有效行；全部失效时回退全部行。

    已失效簇仍需要等级与供应商数用于展示，因此不能因为“没有当前有效提醒”就把
    成员清空。
    """
    live = tuple(alert for alert in event.alerts if alert.is_live)
    return live if live else event.alerts


def _max_level(alerts: Sequence[AlertSnapshot]) -> str | None:
    """最高等级（P1 最高）；无提醒时返回 None。"""
    levels = [alert.level for alert in alerts]
    if not levels:
        return None
    return min(levels, key=lambda level: (LEVEL_ORDER.get(level, len(LEVEL_ORDER)), level))


def _earliest_expiry(alerts: Sequence[AlertSnapshot]) -> datetime | None:
    values = [alert.expires_at for alert in alerts if alert.expires_at is not None]
    return min(values) if values else None


def _member_status(event: EventSnapshot) -> str:
    live_count = sum(1 for alert in event.alerts if alert.is_live)
    if live_count == len(event.alerts):
        return STATUS_ACTIVE
    if live_count == 0:
        return STATUS_EXPIRED
    return STATUS_PARTIALLY_EXPIRED


def _cluster_status(members: Sequence[EventSnapshot]) -> str:
    states = {_member_status(member) for member in members}
    if states == {STATUS_ACTIVE}:
        return STATUS_ACTIVE
    if states == {STATUS_EXPIRED}:
        return STATUS_EXPIRED
    return STATUS_PARTIALLY_EXPIRED


def _expired_reason(event: EventSnapshot) -> str | None:
    """成员失效原因；没有任何失效提醒行时返回 None。"""
    dead = [alert for alert in event.alerts if not alert.is_live]
    if not dead:
        return None
    if event.validity_state == "expired":
        return REASON_EVENT_EXPIRED
    live_alert_ids_by_match: dict[int, set[int]] = {}
    for alert in event.alerts:
        if alert.is_live:
            live_alert_ids_by_match.setdefault(alert.match_id, set()).add(alert.alert_id)
    superseded = any(
        any(
            live_id > alert.alert_id
            for live_id in live_alert_ids_by_match.get(alert.match_id, set())
        )
        for alert in dead
    )
    if superseded:
        return REASON_VERSION_SUPERSEDED
    return REASON_ALERT_EXPIRED


def _primary_region_key(members: Sequence[EventSnapshot]) -> str | None:
    """簇的首选地区键：成员出现次数最多，次数相同取字典序最小，保证稳定。"""
    counts: Counter[str] = Counter()
    for member in members:
        counts.update(member.region_keys)
    if not counts:
        return None
    return min(counts, key=lambda key: (-counts[key], key))


def _region_label(region_key: str | None) -> str:
    """地区键转展示文案：去掉层级前缀后拼接，如 ``city:浙江省:宁波市`` → 浙江省宁波市。"""
    if region_key is None:
        return UNLOCATED_REGION_LABEL
    parts = region_key.split(":")
    label = "".join(part for part in parts[1:] if part)
    return label or parts[0]


def _cluster_start(members: Sequence[EventSnapshot]) -> datetime:
    """簇内最小时间窗起点；全部无法判定时回退事件入库时间。"""
    starts = [member.window[0] for member in members if member.window is not None]
    if not starts:
        starts = [member.created_at for member in members]
    return min(starts)


def _build_member(event: EventSnapshot) -> RiskEventClusterMemberRead:
    basis = _alert_basis(event)
    max_level = _max_level(basis) or "P4"
    return RiskEventClusterMemberRead(
        event_id=event.event_id,
        summary=event.summary,
        source_code=event.source_code,
        source_title=event.source_title,
        published_at=event.published_at,
        valid_from=event.start_at or event.published_at,
        expires_at=_earliest_expiry(basis),
        validity_state=event.validity_state,
        supplier_count=len({alert.supplier_id for alert in basis}),
        max_level=max_level,
        expired_reason=_expired_reason(event),
    )


def _build_cluster(members: Sequence[EventSnapshot]) -> RiskEventClusterRead:
    ordered = sorted(
        members,
        key=lambda member: (
            LEVEL_ORDER.get(_max_level(_alert_basis(member)) or "", len(LEVEL_ORDER)),
            member.event_id,
        ),
    )
    member_views = [_build_member(member) for member in ordered]

    level_counter: Counter[str] = Counter(view.max_level for view in member_views)
    level_counts = [
        LevelCount(level=level, count=level_counter[level])
        for level in sorted(
            level_counter, key=lambda level: LEVEL_ORDER.get(level, len(LEVEL_ORDER))
        )
    ]
    max_level = next(
        (level for level in LEVEL_ORDER if level_counter[level]), "P4"
    )

    live_alerts = [
        alert for member in members for alert in member.alerts if alert.is_live
    ]
    expiry_basis = live_alerts or [alert for member in members for alert in member.alerts]
    expires_values = [
        alert.expires_at for alert in expiry_basis if alert.expires_at is not None
    ]

    region_key = _primary_region_key(members)
    region_label = _region_label(region_key)
    start_date = _cluster_start(members).astimezone(UTC).date().isoformat()

    return RiskEventClusterRead(
        cluster_id=f"{members[0].event_type}:{region_label}:{start_date}",
        event_type=members[0].event_type,
        title=member_views[0].summary if member_views else "",
        region_label=region_label,
        event_count=len(member_views),
        supplier_count=len(
            {
                alert.supplier_id
                for member in members
                for alert in _alert_basis(member)
            }
        ),
        max_level=max_level,
        level_counts=level_counts,
        expires_min=min(expires_values) if expires_values else None,
        expires_max=max(expires_values) if expires_values else None,
        status=_cluster_status(members),
        members=member_views,
    )


def _cluster_sort_key(
    cluster: RiskEventClusterRead,
) -> tuple[int, bool, datetime, int, str]:
    """等级升序（P1 在前）→ 最早截止升序（空值靠后）→ 事件数降序。"""
    return (
        LEVEL_ORDER.get(cluster.max_level, len(LEVEL_ORDER)),
        cluster.expires_min is None,
        cluster.expires_min or _FAR_FUTURE,
        -cluster.event_count,
        cluster.cluster_id,
    )


def _ensure_unique_cluster_ids(clusters: Sequence[RiskEventClusterRead]) -> None:
    """同一 region+日期 上出现多个不相交时间窗簇时补稳定后缀，避免前端 key 冲突。"""
    used: set[str] = set()
    for index, cluster in enumerate(clusters):
        if cluster.cluster_id not in used:
            used.add(cluster.cluster_id)
            continue
        suffix = cluster.members[0].event_id if cluster.members else index
        candidate = f"{cluster.cluster_id}#{suffix}"
        counter = 2
        while candidate in used:
            candidate = f"{cluster.cluster_id}#{suffix}-{counter}"
            counter += 1
        cluster.cluster_id = candidate
        used.add(candidate)


def build_event_clusters(
    snapshots: Sequence[EventSnapshot], *, state: str
) -> list[RiskEventClusterRead]:
    """按口径聚类并排序；同一份数据重复调用结果一致。"""
    pool = [event for event in snapshots if _belongs_to_state(event, state)]
    clusters = [
        _build_cluster(members) for members in group_events(pool) if members
    ]
    clusters.sort(key=_cluster_sort_key)
    _ensure_unique_cluster_ids(clusters)
    return clusters
