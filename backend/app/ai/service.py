from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.providers import PROMPT_VERSION, AIProviderError, get_ai_provider
from app.ai.schemas import SignalAnalysisInput
from app.config import get_ai_settings
from app.signals.models import RawSignal


async def analyze_raw_signal(session: Session, signal: RawSignal) -> AIAnalysisRecord:
    provider = get_ai_provider(get_ai_settings())
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=PROMPT_VERSION,
        status="running",
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    started = perf_counter()

    try:
        result = await provider.analyze_signal(
            SignalAnalysisInput(
                signal_id=signal.id,
                title=signal.title,
                content=signal.content,
                url=signal.url,
                published_at=signal.published_at,
            )
        )
    except AIProviderError as exc:
        session.rollback()
        stored_record = session.get(AIAnalysisRecord, record.id)
        if stored_record is not None:
            stored_record.status = "failed"
            stored_record.finished_at = datetime.now(UTC)
            stored_record.duration_ms = round((perf_counter() - started) * 1000)
            stored_record.error = str(exc)[:2000]
            session.commit()
        raise

    stored_record = session.get(AIAnalysisRecord, record.id)
    assert stored_record is not None
    stored_record.status = "succeeded"
    stored_record.finished_at = datetime.now(UTC)
    stored_record.duration_ms = round((perf_counter() - started) * 1000)
    stored_record.result = result.model_dump(mode="json")
    session.commit()
    session.refresh(stored_record)
    return stored_record
