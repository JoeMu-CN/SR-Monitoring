from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIConfigurationError, AIProviderError
from app.ai.schemas import (
    AIAnalysisRecordListResponse,
    AIAnalysisRecordRead,
    AIStatusRead,
)
from app.ai.service import analyze_raw_signal
from app.config import get_ai_settings
from app.database import get_session
from app.signals.models import RawSignal

router = APIRouter(prefix="/api/v1", tags=["AI 分析"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/ai/status", response_model=AIStatusRead)
def ai_status() -> AIStatusRead:
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
    signal_id: int | None = None,
    record_status: Annotated[str | None, Query(alias="status")] = None,
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


@router.post("/signals/{signal_id}/analyze", response_model=AIAnalysisRecordRead)
async def analyze_signal(signal_id: int, session: SessionDependency) -> AIAnalysisRecord:
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
