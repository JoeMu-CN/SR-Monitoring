from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
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
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.schemas import (
    DashboardSummary,
    EventDetailRead,
    EventSignalEvidence,
    EventTypeCount,
    LevelCount,
    RiskAlertListResponse,
    RiskAlertRead,
    RiskProcessResult,
    SourceHealthRead,
)
from app.risks.service import expire_alerts, process_analysis
from app.signals.models import CollectionRun, DataSource, RawSignal
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
        signal = signals_by_event[event.id]
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
                source_title=signal.title,
                source_url=signal.url,
                published_at=signal.published_at,
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
    return process_analysis(session, signal, analysis)


@router.get("/risk-alerts", response_model=RiskAlertListResponse)
def list_risk_alerts(
    session: SessionDependency,
    _user: RiskView,
    level: Annotated[str | None, Query(pattern=r"^P[1-4]$")] = None,
    alert_status: Annotated[str, Query(alias="status", pattern=r"^(current|expired)$")] = "current",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RiskAlertListResponse:
    filters = [RiskAlert.status == alert_status]
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


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    session: SessionDependency, _user: RiskView
) -> DashboardSummary:
    """风险总览：P1-P4 数量、今日新增、类型分布、最近提醒、数据源状态。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    today_start = now - timedelta(days=1)

    level_rows = session.execute(
        select(RiskAlert.level, func.count())
        .where(RiskAlert.status == "current")
        .group_by(RiskAlert.level)
    ).all()
    level_map = {level: count for level, count in level_rows}
    level_counts = [
        LevelCount(level=level, count=level_map.get(level, 0))
        for level in ("P1", "P2", "P3", "P4")
    ]
    total_current = sum(level_map.values())

    today_new = (
        session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(RiskAlert.status == "current", RiskAlert.created_at >= today_start)
        )
        or 0
    )

    type_rows = session.execute(
        select(RiskEvent.event_type, func.count())
        .join(SupplierEventMatch, SupplierEventMatch.event_id == RiskEvent.id)
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .where(RiskAlert.status == "current")
        .group_by(RiskEvent.event_type)
        .order_by(func.count().desc())
    ).all()
    type_distribution = [
        EventTypeCount(event_type=event_type, count=count)
        for event_type, count in type_rows
    ]

    recent_rows = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(RiskAlert.status == "current")
        .order_by(RiskAlert.updated_at.desc(), RiskAlert.id.desc())
        .limit(10)
    ).all()
    recent_alerts = _build_alert_reads(session, recent_rows)

    sources_list = list(
        session.scalars(select(DataSource).order_by(DataSource.id))
    )
    latest_run_ids = session.execute(
        select(CollectionRun.source_id, func.max(CollectionRun.id))
        .group_by(CollectionRun.source_id)
    ).all()
    latest_by_source = {
        source_id: run_id for source_id, run_id in latest_run_ids
    }
    runs_by_id = {
        run.id: run
        for run in session.scalars(
            select(CollectionRun).where(
                CollectionRun.id.in_(latest_by_source.values() or [0])
            )
        )
    }
    sources = [
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

    return DashboardSummary(
        level_counts=level_counts,
        total_current=total_current,
        today_new=today_new,
        type_distribution=type_distribution,
        recent_alerts=recent_alerts,
        sources=sources,
    )


@router.post("/risk-alerts/expire")
def trigger_expire_alerts(
    session: SessionDependency,
    _user: AnalysisRun,
    _csrf: CsrfGuard,
) -> dict[str, int]:
    expired_count = expire_alerts(session)
    session.commit()
    return {"expired_count": expired_count}
