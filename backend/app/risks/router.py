from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIConfigurationError, AIProviderError
from app.ai.service import analyze_raw_signal
from app.auth.models import User
from app.auth.security import (
    PERM_ANALYSIS_RUN,
    PERM_RISK_VIEW,
    require_permission,
    verify_csrf,
)
from app.database import get_session
from app.risks.dashboard import build_dashboard_aggregate
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.query_validity import current_alert_condition
from app.risks.schemas import (
    DashboardSummary,
    EventDetailRead,
    EventSignalEvidence,
    RiskAlertListResponse,
    RiskAlertRead,
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


def _build_alert_reads(session: Session, rows: Sequence[Any]) -> list[RiskAlertRead]:
    """将 (alert, match, event, supplier) 行组装为响应结构。"""
    event_ids = [event.id for _, _, event, _ in rows]
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

    items: list[RiskAlertRead] = []
    for alert, match, event, supplier in rows:
        # event 仍可能存在但 risk_event_signals 已被 CASCADE 清空（历史信号删除），
        # 此时用安全默认值兜底，避免 500 拖垮整个列表。
        signal = signals_by_event.get(event.id)
        if signal is None:
            source_title = "信号源已失效"
            source_url: str | None = None
            published_at: datetime | None = None
        else:
            source_title = signal.title
            source_url = signal.url
            published_at = signal.published_at
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
                valid_until=event.valid_until,
                review_due_at=event.review_due_at,
                validity_policy_version=event.validity_policy_version,
                validity_reason=ValidityReasonRead.model_validate(event.validity_reason),
                updated_at=alert.updated_at,
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
    analysis = session.scalar(
        select(AIAnalysisRecord)
        .where(
            AIAnalysisRecord.signal_id == signal_id,
            AIAnalysisRecord.status == "succeeded",
            AIAnalysisRecord.result.is_not(None),
        )
        .order_by(AIAnalysisRecord.started_at.desc(), AIAnalysisRecord.id.desc())
    )
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
    filters = [
        current_alert_condition(now_utc)
        if alert_status == "current"
        else RiskAlert.status == "expired"
    ]
    if level:
        filters.append(RiskAlert.level == level)
    total = session.scalar(select(func.count()).select_from(RiskAlert).where(*filters)) or 0
    rows = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(*filters)
        .order_by(RiskAlert.level, RiskAlert.updated_at.desc(), RiskAlert.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = _build_alert_reads(session, rows)
    return RiskAlertListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/risk-alerts/{alert_id}", response_model=RiskAlertRead)
def get_risk_alert(
    alert_id: int, session: SessionDependency, _user: RiskView
) -> RiskAlertRead:
    """风险详情：评分明细、匹配理由、证据与原始来源。"""
    row = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(RiskAlert.id == alert_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险提醒不存在")
    items = _build_alert_reads(session, [row])
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险提醒不存在")
    return items[0]


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
            )
            for signal in signals
        ],
        entities=entities,
        locations=locations,
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
    recent_alerts = _build_alert_reads(session, aggregate.recent_rows)
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


@router.post("/risk-alerts/expire")
def trigger_expire_alerts(
    session: SessionDependency,
    _user: AnalysisRun,
    _csrf: CsrfGuard,
) -> dict[str, int]:
    expired_count = expire_alerts(session, now_utc=datetime.now(UTC))
    session.commit()
    return {"expired_count": expired_count}
