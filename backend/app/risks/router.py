from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Engine, and_, func, or_, select
from sqlalchemy.orm import Session

from app.ai.providers import AIConfigurationError, AIProviderError
from app.ai.service import analyze_raw_signal, latest_reusable_analysis
from app.auth.models import User
from app.auth.security import (
    PERM_ANALYSIS_RUN,
    PERM_RISK_VIEW,
    require_permission,
    verify_csrf,
)
from app.database import get_session
from app.risks.dashboard import (
    CURRENT_LEVELS,
    build_dashboard_aggregate,
    build_dashboard_analytics,
)
from app.risks.event_clusters import (
    STATE_CURRENT,
    build_event_clusters,
    load_event_snapshots,
)
from app.risks.grouping_queries import (
    current_alert_rows as _current_alert_rows,
)
from app.risks.grouping_queries import group_current_rows as _group_current_rows
from app.risks.grouping_queries import (
    representative_rows as _representative_rows,
)
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.query_validity import live_alert_ids
from app.risks.schemas import (
    DashboardAnalytics,
    DashboardSummary,
    EventDetailRead,
    EventSignalEvidence,
    EventSupplierAlertRead,
    LevelCount,
    MergedEventRead,
    RiskAlertListResponse,
    RiskAlertRead,
    RiskAlertSummary,
    RiskEventClusterListResponse,
    RiskEventClusterSummary,
    RiskProcessResult,
    SourceHealthRead,
)
from app.risks.service import InactiveRiskSignalError, expire_alerts, process_analysis
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.schemas import ValidityReasonRead
from app.suppliers.models import Supplier

router = APIRouter(prefix="/api/v1", tags=["风险提醒"])
SessionDependency = Annotated[Session, Depends(get_session)]
RiskView = Annotated[User, Depends(require_permission(PERM_RISK_VIEW))]
AnalysisRun = Annotated[User, Depends(require_permission(PERM_ANALYSIS_RUN))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


def _alert_state(alert: RiskAlert, live_ids: set[int]) -> str:
    """单条提醒的实时状态；判活结果全部来自 SQL 谓词。"""
    return "active" if alert.id in live_ids else "expired"


def _combine_states(states: Sequence[str]) -> str:
    """组/簇状态：全部有效=active，全部失效=expired，混合=partially_expired。"""
    if not states or all(state == "active" for state in states):
        return "active"
    if all(state == "expired" for state in states):
        return "expired"
    return "partially_expired"


def _build_alert_reads(
    session: Session,
    rows: Sequence[Any],
    groups: dict[int, list[tuple[Any, Any, Any, Any]]] | None = None,
    *,
    now_utc: datetime | None = None,
) -> list[RiskAlertRead]:
    """将 (alert, match, event, supplier) 行组装为响应结构。

    ``groups`` 为 alert_id → 同组全部行（含自身）；提供时填充合并展示字段与组级
    口径，不提供则按未合并处理（历史行为）。组级状态与成员状态统一用 ``now_utc``
    下的同一份判活结果，避免相邻请求出现两种口径。
    """
    now = now_utc or datetime.now(UTC)
    grouped_rows = [row for group in (groups or {}).values() for row in group]
    all_rows = list({id(row): row for row in [*rows, *grouped_rows]}.values())
    event_ids = [event.id for _, _, event, _ in all_rows]
    signal_rows = session.execute(
        select(RiskEventSignal.event_id, RawSignal)
        .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
        .where(RiskEventSignal.event_id.in_(event_ids))
        .order_by(
            RiskEventSignal.event_id,
            RawSignal.published_at.desc().nullslast(),
            RawSignal.id.desc(),
        )
    ).all()
    signals_by_event: dict[int, RawSignal] = {}
    for event_id, signal in signal_rows:
        signals_by_event.setdefault(event_id, signal)
    # 组内信源数按事件关联的全部信号统计，而不是只取代表信号的来源。
    source_rows = session.execute(
        select(RiskEventSignal.event_id, RawSignal.source_id)
        .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
        .where(RiskEventSignal.event_id.in_(event_ids))
        .distinct()
    ).all()
    sources_by_event: dict[int, set[int]] = {}
    for event_id, source_id in source_rows:
        sources_by_event.setdefault(event_id, set()).add(source_id)

    live_ids = live_alert_ids(
        session,
        now_utc=now,
        alert_ids=[alert.id for alert, _match, _event, _supplier in all_rows],
    )
    live_ids_by_match: dict[int, set[int]] = {}
    for alert, match, _event, _supplier in all_rows:
        if alert.id in live_ids:
            live_ids_by_match.setdefault(match.id, set()).add(alert.id)

    def _source_of(event: RiskEvent) -> tuple[str, str | None, datetime | None]:
        # event 仍可能存在但 risk_event_signals 已被 CASCADE 清空（历史信号删除），
        # 此时用安全默认值兜底，避免 500 拖垮整个列表。
        signal = signals_by_event.get(event.id)
        if signal is None:
            return "信号源已失效", None, None
        return signal.title, signal.url, signal.published_at

    def _merged_events(alert_id: int) -> list[MergedEventRead]:
        group = (groups or {}).get(alert_id, [])
        items: list[MergedEventRead] = []
        for member_alert, member_match, member_event, _supplier in group:
            if member_alert.id == alert_id:
                continue
            title, url, published = _source_of(member_event)
            live = member_alert.id in live_ids
            # 同一 event×supplier 已有更新版本 current 提醒时，旧版本标记为被取代。
            superseded = (not live) and any(
                live_id > member_alert.id
                for live_id in live_ids_by_match.get(member_match.id, set())
            )
            items.append(
                MergedEventRead(
                    alert_id=member_alert.id,
                    event_id=member_event.id,
                    level=member_alert.level,
                    score=member_alert.score,
                    event_type=member_event.event_type,
                    event_subtype=member_event.event_subtype,
                    event_summary=member_event.summary,
                    event_start_at=member_event.start_at,
                    event_end_at=member_event.end_at,
                    source_title=title,
                    source_url=url,
                    published_at=published,
                    valid_from=member_event.start_at or published,
                    expires_at=member_alert.expires_at,
                    validity_state=member_event.validity_state,
                    status="current" if live else "expired",
                    version_superseded=superseded,
                )
            )
        return items

    items: list[RiskAlertRead] = []
    for alert, match, event, supplier in rows:
        source_title, source_url, published_at = _source_of(event)
        merged = _merged_events(alert.id)
        group_rows = (groups or {}).get(alert.id) or [(alert, match, event, supplier)]
        group_states = [_alert_state(row[0], live_ids) for row in group_rows]
        group_expires = [
            row[0].expires_at for row in group_rows if row[0].expires_at is not None
        ]
        group_source_ids: set[int] = set()
        for row in group_rows:
            group_source_ids.update(sources_by_event.get(row[2].id, set()))
        items.append(
            RiskAlertRead(
                id=alert.id,
                level=alert.level,
                score=alert.score,
                score_detail=alert.score_detail,
                status=alert.status,
                supplier_id=supplier.id,
                supplier_name=supplier.legal_name,
                event_id=event.id,
                event_type=event.event_type,
                event_subtype=event.event_subtype,
                event_summary=event.summary,
                event_start_at=event.start_at,
                event_end_at=event.end_at,
                confidence=event.confidence,
                match_type=match.match_type,
                match_reasons=match.reasons,
                match_evidence=match.evidence,
                source_title=source_title,
                source_url=source_url,
                published_at=published_at,
                expires_at=alert.expires_at,
                expiry_kind=alert.expiry_kind,
                validity_state=event.validity_state,
                processing_state=(
                    signals_by_event[event.id].processing_state
                    if event.id in signals_by_event
                    else None
                ),
                valid_until=event.valid_until,
                review_due_at=event.review_due_at,
                validity_policy_version=event.validity_policy_version,
                validity_reason=ValidityReasonRead.model_validate(event.validity_reason),
                updated_at=alert.updated_at,
                merged_count=len(merged) + 1,
                merged_events=merged,
                group_status=_combine_states(group_states),
                group_event_count=len({row[2].id for row in group_rows}),
                group_source_count=len(group_source_ids),
                group_expires_min=min(group_expires) if group_expires else None,
                group_expires_max=max(group_expires) if group_expires else None,
            )
        )
    return items


@router.post("/signals/{signal_id}/process", response_model=RiskProcessResult)
async def process_signal(
    signal_id: int,
    session: SessionDependency,
    _user: AnalysisRun,
    _csrf: CsrfGuard,
) -> RiskProcessResult:
    signal = session.get(RawSignal, signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险信号不存在")
    analysis = latest_reusable_analysis(session, signal_id)
    if analysis is None:
        try:
            analysis = await analyze_raw_signal(session, signal)
        except AIConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        except AIProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI 分析失败，未生成风险事件",
            ) from exc
    try:
        return process_analysis(session, signal, analysis)
    except InactiveRiskSignalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="风险信号当前无有效证据，未生成风险事件",
        ) from exc


@router.get("/risk-alerts", response_model=RiskAlertListResponse)
def list_risk_alerts(
    session: SessionDependency,
    _user: RiskView,
    level: Annotated[str | None, Query(pattern=r"^P[1-4]$")] = None,
    alert_status: Annotated[str, Query(alias="status", pattern=r"^(current|expired)$")] = "current",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RiskAlertListResponse:
    now_utc = datetime.now(UTC)
    if alert_status == "current":
        rows = _current_alert_rows(session, now_utc=now_utc)
        reps, groups = _representative_rows(session, rows)
        if level:
            reps = [row for row in reps if row[0].level == level]
        reps.sort(
            key=lambda row: (
                # 时间优先：列表默认按卡片上展示的更新时间倒序，等级只做同级次序。
                -row[0].updated_at.timestamp(),
                row[0].level,
                -row[0].id,
            )
        )
        total = len(reps)
        page = reps[offset : offset + limit]
        items = _build_alert_reads(session, page, groups, now_utc=now_utc)
        return RiskAlertListResponse(items=items, total=total, limit=limit, offset=offset)

    now_utc = datetime.now(UTC)
    # 已失效口径必须包含「实时已过期但状态尚未物化」的行，否则重启错过整点时
    # 这些行既不在当前列表、也不在已失效列表，形成查询真空。
    expired_condition = or_(
        RiskAlert.status == "expired",
        and_(RiskAlert.expiry_kind == "finite", RiskAlert.expires_at <= now_utc),
    )
    filters = [expired_condition]
    if level:
        filters.append(RiskAlert.level == level)
    total = session.scalar(select(func.count()).select_from(RiskAlert).where(*filters)) or 0
    expired_rows = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(*filters)
        .order_by(
            RiskAlert.updated_at.desc(), RiskAlert.level, RiskAlert.id.desc()
        )
        .limit(limit)
        .offset(offset)
    ).all()
    items = _build_alert_reads(session, expired_rows, now_utc=now_utc)
    return RiskAlertListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/risk-alerts/summary", response_model=RiskAlertSummary)
def risk_alert_summary(session: SessionDependency, _user: RiskView) -> RiskAlertSummary:
    """当前提醒全量分级计数：不受列表分页限制。

    与 /dashboard/summary 的当前统计共用同一 current_alert_condition 与
    P1-P4 固定四档口径；列表接口只返回单页行数，不得用它的条数冒充当前总量。

    必须声明在 /risk-alerts/{alert_id} 之前：否则 "summary" 会被 int 路径参数
    抢先匹配并以 422 拒绝。

    增量字段（total_events / total_suppliers / total_clusters）与 total_current
    共用同一 now_utc 与同一 current_alert_condition：total_current 仍是合并后的
    风险组数，语义不变；事件、供应商、簇分别按提醒行去重得到。
    """
    now_utc = datetime.now(UTC)
    rows = _current_alert_rows(session, now_utc=now_utc)
    reps, _groups = _representative_rows(session, rows)
    level_map: dict[str, int] = {}
    for alert, _match, _event, _supplier in reps:
        level_map[alert.level] = level_map.get(alert.level, 0) + 1
    clusters = build_event_clusters(
        load_event_snapshots(session, now_utc=now_utc), state=STATE_CURRENT
    )
    return RiskAlertSummary(
        level_counts=[
            LevelCount(level=level, count=level_map.get(level, 0))
            for level in CURRENT_LEVELS
        ],
        total_current=sum(level_map.values()),
        total_events=len({event.id for _alert, _match, event, _supplier in rows}),
        total_suppliers=len({supplier.id for _alert, _match, _event, supplier in rows}),
        total_clusters=len(clusters),
    )


@router.get("/risk-alerts/{alert_id}", response_model=RiskAlertRead)
def get_risk_alert(
    alert_id: int, session: SessionDependency, _user: RiskView
) -> RiskAlertRead:
    """风险详情：评分明细、匹配理由、证据与原始来源（含同组其他事件）。"""
    row = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(RiskAlert.id == alert_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险提醒不存在")
    alert, _match, _event, supplier = row
    now_utc = datetime.now(UTC)
    groups: dict[int, list[Any]] | None = None
    if alert.status == "current":
        # 同一供应商的当前提醒规模很小，按供应商取行再分组即可拿到上下文。
        current_rows = _current_alert_rows(
            session, now_utc=now_utc, supplier_id=supplier.id
        )
        groups = _group_current_rows(session, current_rows)
    items = _build_alert_reads(session, [row], groups, now_utc=now_utc)
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险提醒不存在")
    return items[0]


@router.get("/risk-event-clusters/summary", response_model=RiskEventClusterSummary)
def risk_event_cluster_summary(
    session: SessionDependency, _user: RiskView
) -> RiskEventClusterSummary:
    """当前有效口径的事件/簇/风险组/提醒/供应商计数（同一 as_of）。

    与 /risk-alerts/summary、/dashboard/summary 共用 current_alert_condition：
    - current_alerts：当前有效提醒行数；
    - current_groups：读侧合并后的风险组数（与 total_current 同值）；
    - current_events：提醒行去重后的事件数；
    - current_clusters：事件级聚类后的簇数；
    - current_suppliers：提醒行去重的供应商数。
    """
    now_utc = datetime.now(UTC)
    rows = _current_alert_rows(session, now_utc=now_utc)
    reps, _groups = _representative_rows(session, rows)
    clusters = build_event_clusters(
        load_event_snapshots(session, now_utc=now_utc), state=STATE_CURRENT
    )
    return RiskEventClusterSummary(
        current_events=len({event.id for _alert, _match, event, _supplier in rows}),
        current_clusters=len(clusters),
        current_groups=len(reps),
        current_alerts=len(rows),
        current_suppliers=len(
            {supplier.id for _alert, _match, _event, supplier in rows}
        ),
        as_of=now_utc,
    )


@router.get("/risk-event-clusters", response_model=RiskEventClusterListResponse)
def list_risk_event_clusters(
    session: SessionDependency,
    _user: RiskView,
    state: Annotated[str, Query(pattern=r"^(current|expired)$")] = "current",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RiskEventClusterListResponse:
    """事件簇列表：同事件类型 + 地区相交 + 时间窗重叠的事件归为一簇。

    state=current 取至少有一条实时有效提醒的事件；state=expired 取至少有一条失效
    提醒的事件（status='expired' 或 expires_at 已过，由 SQL 判活谓词给出）。排序为
    等级升序（P1 在前）→ 最早截止升序 → 事件数降序；分页在排序后切片。
    """
    now_utc = datetime.now(UTC)
    clusters = build_event_clusters(
        load_event_snapshots(session, now_utc=now_utc), state=state
    )
    total = len(clusters)
    return RiskEventClusterListResponse(
        items=clusters[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


def _build_event_suppliers(
    session: Session, event_id: int, *, now_utc: datetime
) -> list[EventSupplierAlertRead]:
    """事件详情内一行一个供应商：优先当前有效提醒，否则取最新版本。

    ``version_superseded`` 表示该供应商的这条提醒已被同事件的新版本 current 提醒
    取代（规则版本重算会保留旧 expired 行）；当前有效行本身永远为 False。
    """
    rows = session.execute(
        select(RiskAlert, SupplierEventMatch, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(SupplierEventMatch.event_id == event_id)
        .order_by(RiskAlert.id)
    ).all()
    if not rows:
        return []
    live = live_alert_ids(
        session, now_utc=now_utc, alert_ids=[alert.id for alert, _match, _s in rows]
    )
    latest_by_match: dict[int, tuple[RiskAlert, Supplier]] = {}
    has_live_by_match: dict[int, bool] = {}
    for alert, match, supplier in rows:
        is_live = alert.id in live
        has_live_by_match[match.id] = has_live_by_match.get(match.id, False) or is_live
        current = latest_by_match.get(match.id)
        if current is None:
            latest_by_match[match.id] = (alert, supplier)
            continue
        current_alert = current[0]
        if is_live and current_alert.id not in live:
            latest_by_match[match.id] = (alert, supplier)
        elif is_live == (current_alert.id in live) and alert.id > current_alert.id:
            latest_by_match[match.id] = (alert, supplier)
    views = [
        EventSupplierAlertRead(
            supplier_id=supplier.id,
            supplier_name=supplier.legal_name,
            level=alert.level,
            score=alert.score,
            alert_status=alert.status,
            expires_at=alert.expires_at,
            version_superseded=(alert.id not in live) and has_live_by_match[match_id],
            alert_id=alert.id,
        )
        for match_id, (alert, supplier) in latest_by_match.items()
    ]
    views.sort(
        key=lambda view: (
            (
                CURRENT_LEVELS.index(view.level)
                if view.level in CURRENT_LEVELS
                else len(CURRENT_LEVELS)
            ),
            view.supplier_name,
            view.supplier_id,
        )
    )
    return views


@router.get("/events/{event_id}", response_model=EventDetailRead)
def get_event_detail(
    event_id: int, session: SessionDependency, _user: RiskView
) -> EventDetailRead:
    """事件及全部证据：关联信号、涉及主体、地点。"""
    event = session.get(RiskEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险事件不存在")
    signals = session.execute(
        select(RawSignal)
        .join(RiskEventSignal, RiskEventSignal.signal_id == RawSignal.id)
        .where(RiskEventSignal.event_id == event_id)
        .order_by(RawSignal.published_at.desc().nullslast(), RawSignal.id.desc())
    ).scalars().all()
    entities: list[dict[str, object]] = [
        {
            "name": entity.name,
            "normalized_name": entity.normalized_name,
            "registry_no": entity.registry_no,
        }
        for entity in session.scalars(
            select(EventEntity)
            .where(EventEntity.event_id == event_id)
            .order_by(EventEntity.id)
        )
    ]
    locations: list[dict[str, object]] = [
        {
            "name": location.name,
            "country_code": location.country_code,
            "region": location.region,
            "city": location.city,
            "district": location.district,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "radius_km": location.radius_km,
        }
        for location in session.scalars(
            select(EventLocation)
            .where(EventLocation.event_id == event_id)
            .order_by(EventLocation.id)
        )
    ]
    return EventDetailRead(
        id=event.id,
        dedup_key=event.dedup_key,
        event_type=event.event_type,
        event_subtype=event.event_subtype,
        severity=event.severity,
        summary=event.summary,
        start_at=event.start_at,
        end_at=event.end_at,
        confidence=event.confidence,
        created_at=event.created_at,
        validity_state=event.validity_state,
        valid_until=event.valid_until,
        review_due_at=event.review_due_at,
        validity_policy_version=event.validity_policy_version,
        validity_reason=ValidityReasonRead.model_validate(event.validity_reason),
        signals=[
            EventSignalEvidence(
                signal_id=signal.id,
                title=signal.title,
                content=signal.content,
                url=signal.url,
                published_at=signal.published_at,
                collected_at=signal.collected_at,
                validity_state=signal.validity_state,
                processing_state=signal.processing_state,
            )
            for signal in signals
        ],
        entities=entities,
        locations=locations,
        suppliers=_build_event_suppliers(
            session, event_id, now_utc=datetime.now(UTC)
        ),
    )


def _build_source_health(session: Session) -> list[SourceHealthRead]:
    """来源采集新鲜度：每来源最近一次采集运行的完成时间与状态。"""
    sources_list = list(session.scalars(select(DataSource).order_by(DataSource.id)))
    latest_run_ids = session.execute(
        select(CollectionRun.source_id, func.max(CollectionRun.id)).group_by(
            CollectionRun.source_id
        )
    ).all()
    latest_by_source = {source_id: run_id for source_id, run_id in latest_run_ids}
    runs_by_id = {
        run.id: run
        for run in session.scalars(
            select(CollectionRun).where(
                CollectionRun.id.in_(latest_by_source.values() or [0])
            )
        )
    }
    return [
        SourceHealthRead(
            id=source.id,
            code=source.code,
            name=source.name,
            enabled=source.enabled,
            last_run_at=(
                runs_by_id[latest_by_source[source.id]].finished_at
                if latest_by_source.get(source.id) in runs_by_id
                else None
            ),
            last_run_status=(
                runs_by_id[latest_by_source[source.id]].status
                if latest_by_source.get(source.id) in runs_by_id
                else None
            ),
        )
        for source in sources_list
    ]


def _build_dashboard_summary(session: Session, *, window_days: int) -> DashboardSummary:
    """在单一只读快照事务内完整物化全部总览字段。

    聚合、近期提醒与来源状态全部使用同一 Session；返回的 Pydantic 模型不含
    任何 ORM 实例引用，调用方在快照事务关闭后可安全使用。
    """
    aggregate = build_dashboard_aggregate(session, days=window_days)
    recent_alerts = _build_alert_reads(
        session, aggregate.recent_rows, now_utc=aggregate.as_of
    )
    sources = _build_source_health(session)
    return DashboardSummary(
        level_counts=aggregate.level_counts,
        total_current=aggregate.total_current,
        today_new=aggregate.today_new,
        type_distribution=aggregate.type_distribution,
        recent_alerts=recent_alerts,
        sources=sources,
        as_of=aggregate.as_of,
        window_start=aggregate.window_start,
        window_days=aggregate.window_days,
        period_new_count=aggregate.period_new_count,
        supplier_total=aggregate.supplier_total,
        active_supplier_total=aggregate.active_supplier_total,
        source_distribution=aggregate.source_distribution,
        retention_window_days=aggregate.retention_window_days,
        history_may_be_partial=aggregate.history_may_be_partial,
    )


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    session: SessionDependency,
    _user: RiskView,
    window_days: Annotated[int, Query(alias="days")] = 30,
) -> DashboardSummary:
    """风险总览：当前分布与期间新增分离统计。

    当前统计（P1-P4、今日新增、类型分布、来源分布、最近提醒）共享同一 as_of；
    期间新增统计窗口内创建的全部提醒行（含已失效），不冒充唯一事件数。
    """
    if window_days not in (7, 30, 90):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="days must be 7, 30 or 90",
        )

    bind = session.get_bind()
    if not isinstance(bind, Engine):
        # 调用方（测试夹具或外层调用者）已把 Session 绑定到具体 Connection，
        # 并在该连接上自行管理事务边界；直接在其中完成只读聚合，不另起连接。
        return _build_dashboard_summary(session, window_days=window_days)

    # 认证依赖已在其连接上 commit，之后再对同一 Session 调 connection(execution_options=...)
    # 会被 SQLAlchemy 忽略（"Connection is already established for the given bind"），
    # 隔离级别不会生效。改为从引擎另起一条独立连接，在首次查询前显式声明
    # REPEATABLE READ + 只读事务，使聚合、近期提醒、来源状态同属一个数据库快照，
    # 并发写入在快照建立后提交的行对本请求不可见。
    read_conn = bind.connect().execution_options(
        isolation_level="REPEATABLE READ",
        postgresql_readonly=True,
    )
    try:
        read_txn = read_conn.begin()
        snapshot = Session(bind=read_conn, expire_on_commit=False)
        try:
            return _build_dashboard_summary(snapshot, window_days=window_days)
        finally:
            snapshot.close()
            read_txn.rollback()
    finally:
        read_conn.close()


@router.get("/dashboard/analytics", response_model=DashboardAnalytics)
def dashboard_analytics(
    session: SessionDependency,
    _user: RiskView,
    window_days: Annotated[int, Query(alias="days")] = 30,
) -> DashboardAnalytics:
    """总览大屏补充数据：按日趋势、风险地图点位、供应商城市点位。

    与 /dashboard/summary 分开请求：地图或趋势失败时，KPI、告警流与信源健康
    仍可正常展示。days 白名单与 summary 保持一致。
    """
    if window_days not in (7, 30, 90):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="days must be 7, 30 or 90",
        )
    return build_dashboard_analytics(session, days=window_days)


@router.post("/risk-alerts/expire")
def trigger_expire_alerts(
    session: SessionDependency,
    _user: AnalysisRun,
    _csrf: CsrfGuard,
) -> dict[str, int]:
    expired_count = expire_alerts(session, now_utc=datetime.now(UTC))
    session.commit()
    return {"expired_count": expired_count}
