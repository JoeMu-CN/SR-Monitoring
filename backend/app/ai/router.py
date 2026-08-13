from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIConfigurationError, AIProviderError
from app.ai.schemas import (
    AIAnalysisRecordListResponse,
    AIAnalysisRecordRead,
    AIReviewItemRead,
    AIReviewSummaryRead,
    AIStatusRead,
)
from app.ai.service import analyze_raw_signal
from app.auth.models import User
from app.auth.security import (
    PERM_ANALYSIS_RUN,
    PERM_RISK_VIEW,
    require_permission,
    verify_csrf,
)
from app.config import get_ai_settings
from app.database import get_session
from app.signals.models import RawSignal

router = APIRouter(prefix="/api/v1", tags=["AI 分析"])
SessionDependency = Annotated[Session, Depends(get_session)]
RiskView = Annotated[User, Depends(require_permission(PERM_RISK_VIEW))]
AnalysisRun = Annotated[User, Depends(require_permission(PERM_ANALYSIS_RUN))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


@router.get("/ai/status", response_model=AIStatusRead)
def ai_status(_user: RiskView) -> AIStatusRead:
    settings = get_ai_settings()
    configured = settings.provider == "fake" or (
        settings.provider == "openai-compatible"
        and bool(settings.base_url and settings.model and settings.api_key)
    )
    model = "fake-deterministic-v1" if settings.provider == "fake" else settings.model
    return AIStatusRead(provider=settings.provider, model=model, configured=configured)


@router.get("/ai-analysis-records", response_model=AIAnalysisRecordListResponse)
def list_ai_analysis_records(
    session: SessionDependency,
    _user: RiskView,
    signal_id: int | None = None,
    record_status: Annotated[str | None, Query(alias="status")] = None,
    needs_review: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AIAnalysisRecordListResponse:
    filters = []
    if signal_id is not None:
        filters.append(AIAnalysisRecord.signal_id == signal_id)
    if record_status is not None:
        if record_status not in {"running", "succeeded", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="状态必须是 running、succeeded 或 failed",
            )
        filters.append(AIAnalysisRecord.status == record_status)
    if needs_review is not None:
        filters.append(AIAnalysisRecord.needs_review.is_(needs_review))
    total = (
        session.scalar(select(func.count()).select_from(AIAnalysisRecord).where(*filters))
        or 0
    )
    records = list(
        session.scalars(
            select(AIAnalysisRecord)
            .where(*filters)
            .order_by(AIAnalysisRecord.started_at.desc(), AIAnalysisRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return AIAnalysisRecordListResponse(
        items=[AIAnalysisRecordRead.model_validate(item) for item in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/ai-review-summary", response_model=AIReviewSummaryRead)
def ai_review_summary(session: SessionDependency, _user: RiskView) -> AIReviewSummaryRead:
    review_count = session.scalar(
        select(func.count()).select_from(AIAnalysisRecord).where(AIAnalysisRecord.needs_review.is_(True))
    ) or 0
    filtered_count = session.scalar(
        select(func.count()).select_from(AIAnalysisRecord).where(
            AIAnalysisRecord.provider == "deterministic-filter",
            AIAnalysisRecord.needs_review.is_(True),
        )
    ) or 0
    no_alert_count = session.scalar(
        select(func.count()).select_from(AIAnalysisRecord).where(
            AIAnalysisRecord.review_reason.like("%未匹配到供应商%")
        )
    ) or 0
    return AIReviewSummaryRead(
        needs_review=review_count,
        filtered=filtered_count,
        analyzed_without_alert=no_alert_count,
    )


@router.get("/ai-review-items", response_model=list[AIReviewItemRead])
def ai_review_items(
    session: SessionDependency,
    _user: RiskView,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> list[AIReviewItemRead]:
    rows = session.execute(
        select(AIAnalysisRecord, RawSignal)
        .join(RawSignal, RawSignal.id == AIAnalysisRecord.signal_id)
        .where(AIAnalysisRecord.needs_review.is_(True))
        .order_by(AIAnalysisRecord.started_at.desc(), AIAnalysisRecord.id.desc())
        .limit(limit)
    ).all()
    return [
        AIReviewItemRead(
            id=record.id,
            signal_id=record.signal_id,
            title=signal.title,
            content=signal.content,
            url=signal.url,
            provider=record.provider,
            model=record.model,
            status=record.status,
            started_at=record.started_at,
            review_reason=record.review_reason,
        )
        for record, signal in rows
    ]


@router.post("/signals/{signal_id}/analyze", response_model=AIAnalysisRecordRead)
async def analyze_signal(
    signal_id: int,
    session: SessionDependency,
    _user: AnalysisRun,
    _csrf: CsrfGuard,
) -> AIAnalysisRecord:
    signal = session.get(RawSignal, signal_id)
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风险信号不存在")
    try:
        return await analyze_raw_signal(session, signal)
    except AIConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except AIProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 分析失败，信号保持未解析状态",
        ) from exc
