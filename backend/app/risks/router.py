from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIConfigurationError, AIProviderError
from app.ai.service import analyze_raw_signal
from app.database import get_session
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.schemas import RiskAlertListResponse, RiskAlertRead, RiskProcessResult
from app.risks.service import expire_alerts, process_analysis
from app.signals.models import RawSignal
from app.suppliers.models import Supplier

router = APIRouter(prefix="/api/v1", tags=["风险提醒"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/signals/{signal_id}/process", response_model=RiskProcessResult)
async def process_signal(signal_id: int, session: SessionDependency) -> RiskProcessResult:
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
    return RiskAlertListResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("/risk-alerts/expire")
def trigger_expire_alerts(session: SessionDependency) -> dict[str, int]:
    expired_count = expire_alerts(session)
    session.commit()
    return {"expired_count": expired_count}
