from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import Text, and_, cast, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.agent.tyc_analysis_context import (
    load_analysis_context,
    render_analysis_context,
)
from app.ai.models import AIAnalysisRecord
from app.ai.providers import PROMPT_VERSION, AIProviderError, get_ai_provider
from app.ai.schemas import SignalAnalysisInput
from app.config import get_ai_settings
from app.signals.models import RawSignal
from app.signals.validity import ProcessingState


def usable_analysis_result() -> ColumnElement[bool]:
    """``result`` 是否携带可复用的真实分析结果（SQL 非 NULL 且不是 JSON null）。

    ``result`` 是无 ``none_as_null`` 的 JSONB 列，显式 ``None`` 落库为 JSON null
    而非 SQL NULL，因此 ``is_(None)`` / ``is_not(None)`` 无法区分空结果与真实对象。
    用 ``CAST(result AS TEXT)`` 做跨方言（Postgres JSONB 与 SQLite）判定：
    真实对象渲染为 ``{...}`` 永不等于 ``null``；SQL NULL 行已被首个条件排除，
    三值逻辑下整表达式不会为 UNKNOWN。
    """
    return and_(
        AIAnalysisRecord.result.is_not(None),
        cast(AIAnalysisRecord.result, Text) != "null",
    )


def latest_reusable_analysis(
    session: Session, signal_id: int
) -> AIAnalysisRecord | None:
    """该信号最近一次可复用的成功分析；确定性过滤等空结果记录不算。"""
    return session.scalar(
        select(AIAnalysisRecord)
        .where(
            AIAnalysisRecord.signal_id == signal_id,
            AIAnalysisRecord.status == "succeeded",
            usable_analysis_result(),
        )
        .order_by(AIAnalysisRecord.started_at.desc(), AIAnalysisRecord.id.desc())
    )


def analysis_content(signal: RawSignal) -> str:
    """LLM 输入正文：天眼查画像用私有上下文 JSON，普通信号沿用既有 content。

    上下文缺失或损坏时回落 ``signal.content``，因此历史数据与普通信号行为完全不变；
    仍然只有一次 LLM 调用，规则与判定链路不受影响。
    """
    context = load_analysis_context(signal.raw_data)
    return render_analysis_context(context) if context is not None else signal.content


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
                content=analysis_content(signal),
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
            stored_record.needs_review = True
            stored_record.review_reason = "AI 分类最终失败，需人工复核"
            stored_signal = session.get(RawSignal, signal.id)
            if (
                stored_signal is not None
                and stored_signal.validity_state is None
                and stored_signal.processing_state
                == ProcessingState.PENDING_CLASSIFICATION
            ):
                # 生命周期保持 NULL，失败只落在处理阶段（不改 validity_state）。
                stored_signal.processing_state = ProcessingState.CLASSIFICATION_FAILED
                stored_signal.validity_reason = {
                    "code": "classification_failed",
                    "anchor_source": (
                        "published_at"
                        if stored_signal.published_at is not None
                        else "collected_at"
                    ),
                    "details": {"analysis_id": stored_record.id},
                }
            session.commit()
        raise

    stored_record = session.get(AIAnalysisRecord, record.id)
    assert stored_record is not None
    stored_record.status = "succeeded"
    stored_record.finished_at = datetime.now(UTC)
    stored_record.duration_ms = round((perf_counter() - started) * 1000)
    stored_record.result = result.model_dump(mode="json")
    review_reasons: list[str] = []
    if result.confidence < 0.65:
        review_reasons.append(f"AI 置信度偏低（{result.confidence:.2f}）")
    if result.event_type == "other":
        review_reasons.append("风险大类为 other")
    if result.event_subtype is None:
        review_reasons.append("未识别出兼容的风险细类")
    stored_record.needs_review = bool(review_reasons)
    stored_record.review_reason = "；".join(review_reasons) or None
    session.commit()
    session.refresh(stored_record)
    return stored_record
