"""信号状态模型拆分后的写入侧契约：生命周期与处理阶段互不覆盖。

覆盖 6 条写入语义（与 0066 迁移一一对应）：
1. 策略未定入库 → ``validity_state=NULL`` + ``processing_state='pending_classification'``；
2. 策略解析成功 → ``validity_state='active'`` + 仍是待分类阶段；
3. 确定性过滤 / 行情未触发 → 只改处理阶段（filtered / no_trigger），生命周期不变；
4. 分析成功进入事件链 → ``processing_state='analyzed'``；
5. 分类失败 → ``processing_state='classification_failed'``，生命周期保持原值（未定时 NULL）；
6. 过期/替代/撤销/冲突 → 只改生命周期，处理阶段不动。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.scheduler.jobs as scheduler_jobs
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.market.config_store import invalidate_market_config_cache
from app.risks.service import process_analysis
from app.risks.signal_validity import classify_pending_signal, expire_signal_if_due
from app.signals.identity import compute_dedup_key
from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    persist_signal_ingestions,
)
from app.signals.models import DataSource, RawSignal
from app.signals.prefilter import prefilter_new_signals
from app.signals.relevance import invalidate_filter_rules_cache
from app.signals.schemas import ManualSignalInput
from app.signals.validity import ProcessingState, ValidityState

NOW_UTC: Final = datetime(2026, 10, 22, 12, tzinfo=UTC)
CU_SYMBOL: Final = "nf_CU0"


def _source(
    session: Session,
    code: str,
    *,
    validity_policy: dict[str, object] | None = None,
) -> DataSource:
    """取或建测试信源（迁移已 seed 的 code 直接复用，避免唯一键冲突）。"""
    source = session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
        source = DataSource(
            code=code,
            name=code,
            source_type="api",
            credibility=80,
            enabled=True,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
        )
        session.add(source)
    source.enabled = True
    if validity_policy is not None:
        source.validity_policy = dict(validity_policy)
    session.flush()
    return source


def _ingestion(
    source: DataSource,
    *,
    suffix: str,
    validity_profile: str | None = None,
) -> SignalIngestion:
    signal = ManualSignalInput(
        external_id=f"processing-{suffix}",
        title=f"处理状态用例信号 {suffix}",
        content="测试供应商有限公司受台风影响暂停生产。",
        published_at=NOW_UTC - timedelta(hours=1),
        validity_profile=validity_profile,
    )
    return SignalIngestion(
        source=source,
        signal=signal,
        fingerprint=f"processing-{suffix}",
        dedup_key=compute_dedup_key(
            source.code,
            external_id=signal.external_id,
            url=None,
            title=signal.title,
        ),
        collected_at=NOW_UTC,
        authority=LifecycleAuthority("adapter", "test-adapter"),
    )


def _stored_signal(session: Session, fingerprint: str) -> RawSignal:
    signal = session.scalar(select(RawSignal).where(RawSignal.fingerprint == fingerprint))
    assert signal is not None
    session.refresh(signal)
    return signal


def _weather_result() -> SignalAnalysisResult:
    return SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="high",
        organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        locations=[],
        affected_activities=["production"],
        affected_products=[],
        start_at=NOW_UTC - timedelta(hours=2),
        summary_zh="台风影响测试供应商生产",
        evidence_sentences=["测试供应商有限公司受台风影响暂停生产。"],
        confidence=0.9,
    )


def _analysis_record(
    session: Session,
    signal: RawSignal,
    *,
    result: SignalAnalysisResult | None,
) -> AIAnalysisRecord:
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="processing-test",
        model="processing-v1",
        prompt_version="processing-v1",
        status="succeeded",
        finished_at=NOW_UTC,
        result=result.model_dump(mode="json") if result is not None else None,
    )
    session.add(record)
    session.flush()
    return record


def _insert_signal(
    session: Session,
    source: DataSource,
    suffix: str,
    *,
    processing: ProcessingState,
    validity: str | None = None,
    valid_until: datetime | None = None,
    raw_data: dict[str, object] | None = None,
) -> RawSignal:
    """按指定「生命周期 + 处理阶段」组合直插一行（约束校验在 flush 时真实生效）。"""
    resolved = validity is not None
    signal = RawSignal(
        source_id=source.id,
        external_id=f"processing-row-{suffix}",
        title=f"处理状态直插信号 {suffix}",
        content="沪铜主力 nf_CU0 收盘 70100 元/吨，较昨结算：+0.10%。",
        published_at=NOW_UTC - timedelta(hours=1),
        collected_at=NOW_UTC,
        fingerprint=f"processing-row-{suffix}",
        dedup_key=f"processing-row-{suffix}",
        raw_data=raw_data or {},
        validity_profile="weather_alert" if resolved else None,
        validity_state=validity,
        processing_state=processing,
        valid_from=(NOW_UTC - timedelta(hours=1)) if resolved else None,
        valid_until=valid_until,
        validity_mode="fixed_days" if resolved else None,
        validity_policy_version="policy-processing" if resolved else None,
        validity_reason={
            "code": "policy_resolved" if resolved else "pending_classification",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


# ---------------------------------------------------------------------------
# 1 / 2：入库阶段（策略未定 → NULL + pending；策略解析 → active + pending）
# ---------------------------------------------------------------------------


def test_ingestion_without_policy_writes_null_lifecycle_and_pending_stage(
    db_session: Session,
) -> None:
    # Given：信源与信号都没有显式有效期策略，必须等 AI 分类。
    source = _source(db_session, "processing-ingest-pending")

    # When
    assert persist_signal_ingestions(
        db_session, [_ingestion(source, suffix="pending")]
    ) == 1

    # Then：生命周期未定写 NULL，处理阶段落在待分类，reason code 不变。
    signal = _stored_signal(db_session, "processing-pending")
    assert signal.validity_state is None
    assert signal.processing_state == ProcessingState.PENDING_CLASSIFICATION
    assert signal.validity_reason["code"] == "pending_classification"
    assert signal.validity_mode is None
    assert signal.valid_until is None


def test_ingestion_with_resolved_policy_writes_active_lifecycle_and_pending_stage(
    db_session: Session,
) -> None:
    # Given：信源带显式 fixed_days 策略，信号带 profile。
    source = _source(
        db_session,
        "processing-ingest-resolved",
        validity_policy={"mode": "fixed_days", "fixed_days": 30},
    )

    # When
    assert persist_signal_ingestions(
        db_session,
        [_ingestion(source, suffix="resolved", validity_profile="other")],
    ) == 1

    # Then：生命周期已定（active），但处理阶段仍是待分类，等分析推进。
    signal = _stored_signal(db_session, "processing-resolved")
    assert signal.validity_state == ValidityState.ACTIVE
    assert signal.processing_state == ProcessingState.PENDING_CLASSIFICATION
    assert signal.validity_reason["code"] == "policy_resolved"


# ---------------------------------------------------------------------------
# 3：确定性过滤 / 行情未触发只改处理阶段，生命周期保持原值
# ---------------------------------------------------------------------------


def test_prefilter_filter_keeps_lifecycle_and_sets_filtered(db_session: Session) -> None:
    # Given：生命周期已 active 的清单类信号，文本不命中任何供应商。
    invalidate_filter_rules_cache()
    source = _source(db_session, "ofac-sdn")
    signal = _insert_signal(
        db_session,
        source,
        "prefilter-active",
        processing=ProcessingState.PENDING_CLASSIFICATION,
        validity="active",
        valid_until=NOW_UTC + timedelta(days=3),
    )

    # When
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW_UTC)

    # Then：处理阶段 filtered，生命周期仍是 active（不被过滤结论覆盖）。
    assert outcome.filtered == 1
    db_session.refresh(signal)
    assert signal.processing_state == ProcessingState.FILTERED
    assert signal.validity_state == ValidityState.ACTIVE
    assert signal.validity_reason["code"] == "filtered_out"
    assert signal.validity_reason["details"]["filter"]


def test_prefilter_filter_keeps_null_lifecycle(db_session: Session) -> None:
    # Given：生命周期未定、待分类的清单类信号。
    invalidate_filter_rules_cache()
    source = _source(db_session, "ofac-sdn")
    signal = _insert_signal(
        db_session, source, "prefilter-null", processing=ProcessingState.PENDING_CLASSIFICATION
    )

    # When
    outcome = prefilter_new_signals(db_session, [signal.id], now_utc=NOW_UTC)

    # Then：只落处理阶段，生命周期仍为 NULL（不会写入已废弃的 irrelevant）。
    assert outcome.filtered == 1
    db_session.refresh(signal)
    assert signal.processing_state == ProcessingState.FILTERED
    assert signal.validity_state is None


def test_market_not_triggered_sets_no_trigger_and_keeps_lifecycle(
    db_session: Session,
) -> None:
    # Given：已登记指标（沪铜）的单点读数，涨幅远低于触发线，生命周期 active。
    invalidate_market_config_cache()
    source = _source(db_session, "commodity-futures")
    signal = _insert_signal(
        db_session,
        source,
        "market-no-trigger",
        processing=ProcessingState.PENDING_CLASSIFICATION,
        validity="active",
        valid_until=NOW_UTC + timedelta(days=3),
        raw_data={
            "title": "沪铜主力 日行情",
            "source_extra": {"symbol": CU_SYMBOL, "last_price": 70100.0, "change_pct": 0.1},
        },
    )
    db_session.commit()

    # When：直接走消费链的行情分支（不经过 AI）。
    try:
        record, is_terminal = scheduler_jobs._evaluate_market_signal(
            db_session, signal, source.code, now_utc=NOW_UTC
        )
    finally:
        invalidate_market_config_cache()

    # Then：未触发 → no_trigger；生命周期保持 active，不写 irrelevant。
    assert record is None
    assert is_terminal is True
    db_session.refresh(signal)
    assert signal.processing_state == ProcessingState.NO_TRIGGER
    assert signal.validity_state == ValidityState.ACTIVE
    assert signal.validity_reason["code"] == "filtered_out"
    assert signal.validity_reason["details"]["filter"] == scheduler_jobs.MARKET_FILTER_KIND


# ---------------------------------------------------------------------------
# 4：分析成功进入事件链 → analyzed
# ---------------------------------------------------------------------------


def test_analysis_success_sets_analyzed_after_classification(db_session: Session) -> None:
    # Given：生命周期未定（待分类）的信号 + 明确 AI 细类。
    source = _source(db_session, "processing-analysis")
    signal = _insert_signal(
        db_session, source, "analysis", processing=ProcessingState.PENDING_CLASSIFICATION
    )
    analysis = _analysis_record(db_session, signal, result=_weather_result())

    # When
    outcome = process_analysis(db_session, signal, analysis, now_utc=NOW_UTC)

    # Then：分类补出 active，进入事件链后处理阶段 analyzed。
    assert outcome.event_created is True
    db_session.refresh(signal)
    assert signal.validity_state == ValidityState.ACTIVE
    assert signal.processing_state == ProcessingState.ANALYZED


# ---------------------------------------------------------------------------
# 5：分类失败只改处理阶段
# ---------------------------------------------------------------------------


def test_classification_failure_sets_failed_stage_with_null_lifecycle(
    db_session: Session,
) -> None:
    # Given：信源与信号都没有可推导的 profile，AI 也没给出细类。
    source = _source(db_session, "processing-classify-failed")
    signal = _insert_signal(
        db_session, source, "classify-failed", processing=ProcessingState.PENDING_CLASSIFICATION
    )
    result = _weather_result().model_copy(update={"event_type": "other", "event_subtype": None})
    analysis = _analysis_record(db_session, signal, result=result)

    # When
    classify_pending_signal(db_session, signal, analysis, result, now_utc=NOW_UTC)
    db_session.flush()

    # Then：处理阶段 classification_failed，生命周期仍是 NULL。
    assert signal.processing_state == ProcessingState.CLASSIFICATION_FAILED
    assert signal.validity_state is None
    assert signal.validity_reason["code"] == "classification_failed"
    assert signal.validity_reason["details"]["reason"] == "event_subtype_missing"
    assert analysis.needs_review is True


# ---------------------------------------------------------------------------
# 6：过期只改生命周期
# ---------------------------------------------------------------------------


def test_expiry_only_touches_lifecycle(db_session: Session) -> None:
    # Given：已分析且已到期的 active 信号。
    source = _source(db_session, "processing-expiry")
    signal = _insert_signal(
        db_session,
        source,
        "expiry",
        processing=ProcessingState.ANALYZED,
        validity="active",
        valid_until=NOW_UTC - timedelta(hours=1),
    )

    # When
    expired = expire_signal_if_due(signal, now_utc=NOW_UTC)

    # Then：只改生命周期，处理阶段保持 analyzed。
    assert expired is True
    assert signal.validity_state == ValidityState.EXPIRED
    assert signal.processing_state == ProcessingState.ANALYZED
    assert signal.validity_reason["code"] == "deadline_reached"
