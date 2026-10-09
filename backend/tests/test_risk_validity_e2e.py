"""采集、AI 分类、事件归并与提醒失效的有效证据生命周期。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from pytest import MonkeyPatch
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import app.scheduler.jobs as scheduler_jobs
import app.scheduler.runtime as scheduler_runtime
from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.providers import PROMPT_VERSION, AIProviderError, FakeAIProvider
from app.ai.schemas import SignalAnalysisInput, SignalAnalysisResult
from app.database import SessionLocal
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.service import expire_alerts, process_analysis
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

NOW_UTC = datetime(2026, 9, 6, 12, tzinfo=UTC)


def _source(session: Session, code: str) -> DataSource:
    source = DataSource(
        code=code,
        name=code,
        source_type="api",
        credibility=90,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    session.add(source)
    session.flush()
    return source


def _signal(
    session: Session,
    source: DataSource,
    suffix: str,
    published_at: datetime,
    *,
    state: str = "pending_classification",
    valid_until: datetime | None = None,
    review_due_at: datetime | None = None,
) -> RawSignal:
    resolved = state != "pending_classification"
    signal = RawSignal(
        source_id=source.id,
        external_id=suffix,
        title=f"台风预警 {suffix}",
        content="测试供应商有限公司受台风影响暂停生产。",
        published_at=published_at,
        collected_at=NOW_UTC,
        fingerprint=f"risk-validity-{suffix}",
        raw_data={},
        validity_profile="weather_alert" if resolved else None,
        validity_state=state,
        valid_from=published_at,
        valid_until=valid_until,
        review_due_at=review_due_at,
        validity_mode="fixed_days" if resolved else None,
        lifecycle_action="assert",
        validity_policy_version=f"policy-{suffix}" if resolved else None,
        validity_reason={
            "code": "policy_resolved" if resolved else "pending_classification",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
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
        start_at=datetime(2026, 9, 6, 8, tzinfo=UTC),
        summary_zh="台风影响测试供应商生产",
        evidence_sentences=["测试供应商有限公司受台风影响暂停生产。"],
        confidence=0.9,
    )


def _analysis(session: Session, signal: RawSignal) -> AIAnalysisRecord:
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="validity-test",
        model="validity-v1",
        prompt_version="validity-v1",
        status="succeeded",
        finished_at=NOW_UTC,
        result=_weather_result().model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return record


def _supplier(session: Session, code: str) -> None:
    session.add(
        Supplier(
            supplier_code=code,
            legal_name="测试供应商有限公司",
            country_code="CN",
        )
    )
    session.flush()


def test_scheduler_skips_signal_already_expired_at_collection(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    source = _source(db_session, "validity-expired")
    _signal(
        db_session,
        source,
        "ten-days-old",
        NOW_UTC - timedelta(days=10),
        state="expired",
        valid_until=NOW_UTC - timedelta(days=7),
    )
    calls = 0

    async def unexpected_analysis(_session: Session, _signal: RawSignal) -> AIAnalysisRecord:
        nonlocal calls
        calls += 1
        raise AssertionError("expired signal reached AI")

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "analyze_raw_signal", unexpected_analysis)

    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 0
    assert calls == 0
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0


def test_pending_classification_becomes_active_or_expired_before_event(
    db_session: Session,
) -> None:
    source = _source(db_session, "validity-classification")
    _supplier(db_session, "VALIDITY-CLASSIFY")
    current = _signal(db_session, source, "current", NOW_UTC - timedelta(days=1))
    stale = _signal(db_session, source, "stale", NOW_UTC - timedelta(days=10))

    result = process_analysis(db_session, current, _analysis(db_session, current), now_utc=NOW_UTC)
    with pytest.raises(ValueError):
        process_analysis(db_session, stale, _analysis(db_session, stale), now_utc=NOW_UTC)

    assert result.event_created is True
    assert current.validity_state == "active"
    assert current.valid_until == current.published_at + timedelta(days=3)
    assert stale.validity_state == "expired"
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskEventSignal)) == 1


def test_final_ai_failure_stays_pending_for_manual_review(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    source = _source(db_session, "validity-ai-failure")
    signal = _signal(db_session, source, "classification-failed", NOW_UTC)

    class FailingProvider:
        provider_name = "failing-test"
        model = "failing-v1"

        async def analyze_signal(self, _value: SignalAnalysisInput) -> SignalAnalysisResult:
            raise AIProviderError("final failure")

    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: FailingProvider())
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 0
    record = db_session.scalar(select(AIAnalysisRecord))
    assert record is not None and record.status == "failed" and record.needs_review is True
    assert signal.validity_state == "pending_classification"
    assert signal.validity_reason["code"] == "classification_failed"
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0


def test_succeeded_ai_is_reclaimed_after_event_processing_interrupt(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    source = _source(db_session, "validity-retry")
    _supplier(db_session, "VALIDITY-RETRY")
    signal = _signal(db_session, source, "retry", NOW_UTC - timedelta(hours=1))
    _analysis(db_session, signal)
    db_session.commit()
    attempts = 0

    def interrupt(
        _session: Session,
        _signal: RawSignal,
        _analysis: AIAnalysisRecord,
        *,
        now_utc: datetime,
    ) -> None:
        del now_utc
        nonlocal attempts
        attempts += 1
        raise RuntimeError("interrupted after AI success")

    async def unexpected_analysis(_session: Session, _signal: RawSignal) -> AIAnalysisRecord:
        raise AssertionError("succeeded AI result must be reused")

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "process_analysis", interrupt)
    monkeypatch.setattr(scheduler_jobs, "analyze_raw_signal", unexpected_analysis)
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 0
    monkeypatch.setattr(scheduler_jobs, "process_analysis", process_analysis)
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 1

    assert attempts == 1
    assert db_session.scalar(select(func.count()).select_from(AIAnalysisRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskEventSignal)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 1


def test_succeeded_v2_analysis_is_reused_without_v3_reanalysis(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """历史 succeeded（v2，缺新字段）继续复用：不触发 provider，也不追加 v3 记录。"""
    source = _source(db_session, "reuse-v2-level")
    _supplier(db_session, "VALIDITY-REUSE-V2")
    signal = _signal(db_session, source, "reuse-v2", NOW_UTC - timedelta(hours=1))
    legacy_result = _weather_result().model_dump(mode="json")
    del legacy_result["suggested_level"]
    del legacy_result["level_rationale"]
    db_session.add(
        AIAnalysisRecord(
            signal_id=signal.id,
            provider="legacy-provider",
            model="legacy-v1",
            prompt_version="signal-analysis-v2",
            status="succeeded",
            finished_at=NOW_UTC,
            duration_ms=10,
            result=legacy_result,
        )
    )
    db_session.commit()
    provider_calls = 0

    async def unexpected_analysis(
        _session: Session, _signal: RawSignal
    ) -> AIAnalysisRecord:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("历史 succeeded 分析必须复用，不得重算")

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(scheduler_jobs, "analyze_raw_signal", unexpected_analysis)
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 1
    assert provider_calls == 0
    records = list(db_session.scalars(select(AIAnalysisRecord)))
    assert [record.prompt_version for record in records] == ["signal-analysis-v2"]


def test_new_pending_candidate_is_analyzed_with_current_prompt_and_level_suggestion(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """从未成功分析的候选：走当前提示版本分析并落对应 prompt_version。"""
    source = _source(db_session, "fresh-v3-level")
    _supplier(db_session, "VALIDITY-FRESH-V3")
    _signal(db_session, source, "fresh-v3", NOW_UTC - timedelta(hours=1))
    db_session.commit()
    suggested = _weather_result().model_copy(
        update={
            "suggested_level": "P2",
            "level_rationale": "文本明确提及台风导致停产，建议 P2。",
        }
    )
    monkeypatch.setattr(
        ai_service, "get_ai_provider", lambda _settings: FakeAIProvider(result=suggested)
    )
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 1

    record = db_session.scalar(select(AIAnalysisRecord))
    assert record is not None
    assert record.status == "succeeded"
    assert record.prompt_version == PROMPT_VERSION
    assert record.result is not None
    assert record.result["suggested_level"] == "P2"
    assert record.result["level_rationale"] == "文本明确提及台风导致停产，建议 P2。"


def test_signal_linked_to_event_is_excluded_from_analysis_queue(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """已关联风险事件的信号不再进入分析队列（不重算、不调用 provider）。"""
    source = _source(db_session, "linked-skip")
    signal = _signal(db_session, source, "linked-skip", NOW_UTC - timedelta(hours=1))
    event = RiskEvent(
        dedup_key="linked-skip-event",
        event_type="weather",
        severity="high",
        summary="已归并事件",
        confidence=0.9,
        facts={},
        validity_state="active",
        validity_reason={
            "code": "effective_signal_support",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    db_session.commit()
    provider_calls = 0

    async def unexpected_analysis(
        _session: Session, _signal: RawSignal
    ) -> AIAnalysisRecord:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("已关联事件的信号不得重新分析")

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(scheduler_jobs, "analyze_raw_signal", unexpected_analysis)

    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC) == 0
    assert provider_calls == 0
    assert db_session.scalar(select(AIAnalysisRecord)) is None


def test_alert_expires_at_support_deadline_and_only_valid_evidence_extends_it(
    db_session: Session,
) -> None:
    source = _source(db_session, "validity-alert")
    _supplier(db_session, "VALIDITY-ALERT")
    first_deadline = NOW_UTC + timedelta(days=2)
    first = _signal(
        db_session,
        source,
        "first",
        NOW_UTC,
        state="active",
        valid_until=first_deadline,
    )
    process_analysis(db_session, first, _analysis(db_session, first), now_utc=NOW_UTC)
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None and alert.expires_at == first_deadline

    assert expire_alerts(db_session, now_utc=first_deadline) == 1
    assert alert.status == "expired"

    official_deadline = NOW_UTC + timedelta(days=5)
    official = _signal(
        db_session,
        source,
        "official",
        NOW_UTC + timedelta(days=2),
        state="active",
        valid_until=official_deadline,
    )
    process_analysis(db_session, official, _analysis(db_session, official), now_utc=first_deadline)
    assert alert.status == "current" and alert.expires_at == official_deadline

    for state in ("expired", "revoked"):
        invalid = _signal(
            db_session,
            source,
            state,
            NOW_UTC + timedelta(days=3),
            state=state,
            valid_until=NOW_UTC + timedelta(days=30),
        )
        with pytest.raises(ValueError):
            process_analysis(
                db_session,
                invalid,
                _analysis(db_session, invalid),
                now_utc=first_deadline,
            )
    assert alert.expires_at == official_deadline
    assert db_session.scalar(select(func.count()).select_from(RiskEventSignal)) == 2


def test_concurrent_same_dedup_keeps_both_links_without_duplicate_alert(
    monkeypatch: MonkeyPatch,
) -> None:
    code = "validity-concurrent"
    with SessionLocal() as setup:
        source = _source(setup, code)
        _supplier(setup, "VALIDITY-CONCURRENT")
        signals = [
            _signal(
                setup,
                source,
                f"concurrent-{index}",
                NOW_UTC,
                state="active",
                valid_until=NOW_UTC + timedelta(days=3),
            )
            for index in range(2)
        ]
        analyses = [_analysis(setup, signal) for signal in signals]
        ids = [(signal.id, analysis.id) for signal, analysis in zip(signals, analyses, strict=True)]
        source_id = source.id
        setup.commit()

    barrier = Barrier(2)
    original_scalar = Session.scalar

    def coordinated_scalar(self: Session, statement):
        value = original_scalar(self, statement)
        if (
            "FROM risk_events" in str(statement)
            and "risk_events.dedup_key" in str(statement)
            and value is None
        ):
            barrier.wait(timeout=5)
        return value

    def run(item: tuple[int, int]) -> None:
        with SessionLocal() as session:
            signal = session.get(RawSignal, item[0])
            analysis = session.get(AIAnalysisRecord, item[1])
            assert signal is not None and analysis is not None
            process_analysis(session, signal, analysis, now_utc=NOW_UTC)

    monkeypatch.setattr(Session, "scalar", coordinated_scalar)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(run, ids))
        with SessionLocal() as verify:
            test_event_ids = (
                select(RiskEventSignal.event_id)
                .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
                .where(RawSignal.source_id == source_id)
            )
            assert (
                verify.scalar(
                    select(func.count()).select_from(RiskEvent).where(RiskEvent.id.in_(test_event_ids))
                )
                == 1
            )
            assert (
                verify.scalar(
                    select(func.count())
                    .select_from(RiskEventSignal)
                    .where(RiskEventSignal.event_id.in_(test_event_ids))
                )
                == 2
            )
            assert (
                verify.scalar(
                    select(func.count())
                    .select_from(RiskAlert)
                    .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
                    .where(SupplierEventMatch.event_id.in_(test_event_ids))
                )
                == 1
            )
    finally:
        monkeypatch.setattr(Session, "scalar", original_scalar)
        with SessionLocal() as cleanup:
            test_signal_ids = select(RawSignal.id).where(RawSignal.source_id == source_id)
            test_event_ids = select(RiskEventSignal.event_id).where(
                RiskEventSignal.signal_id.in_(test_signal_ids)
            )
            test_match_ids = select(SupplierEventMatch.id).where(
                SupplierEventMatch.event_id.in_(test_event_ids)
            )
            cleanup.execute(delete(RiskAlert).where(RiskAlert.match_id.in_(test_match_ids)))
            cleanup.execute(
                delete(SupplierEventMatch).where(SupplierEventMatch.id.in_(test_match_ids))
            )
            cleanup.execute(delete(RiskEvent).where(RiskEvent.id.in_(test_event_ids)))
            cleanup.execute(
                delete(AIAnalysisRecord).where(AIAnalysisRecord.signal_id.in_(test_signal_ids))
            )
            cleanup.execute(delete(RawSignal).where(RawSignal.id.in_(test_signal_ids)))
            cleanup.execute(delete(Supplier).where(Supplier.supplier_code == "VALIDITY-CONCURRENT"))
            cleanup.execute(delete(DataSource).where(DataSource.code == code))
            cleanup.commit()
