"""AI 分析结果的 NULL 语义：JSON null / SQL NULL 的过滤、复用与队首不阻塞。

确定性过滤（deterministic-filter）以 ``result=None`` 落库，而 ``result`` 是无
``none_as_null`` 的 JSONB 列，显式 None 实际写成 JSON null（``jsonb_typeof='null'``）
而非 SQL NULL。因此 ``result.is_(None)`` 排除不掉这些已过滤记录，
``result.is_not(None)`` 又把它们当作可复用分析（静默 continue）。
本文件锁定正确的 NULL 语义：过滤记录（JSON null 或 SQL NULL）不可复用；0066 起队列
是否捞取只由 ``processing_state`` 决定（过滤结论必须显式落 filtered），真实 JSON
object 结果必须可复用，队首过滤记录不得阻塞后续信号，手工 process 路径一致。
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, insert, null, select
from sqlalchemy.orm import Session

import app.scheduler.jobs as scheduler_jobs
from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.providers import FakeAIProvider
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import RiskEventSignal
from app.signals.models import DataSource, RawSignal
from app.signals.validity import ProcessingState

NOW_UTC = datetime(2026, 10, 10, 12, tzinfo=UTC)


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
    session: Session, source: DataSource, suffix: str, published_at: datetime
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=suffix,
        title=f"港口管制 {suffix}",
        content="测试供应商有限公司受港口管制影响。",
        published_at=published_at,
        collected_at=published_at,
        fingerprint=f"null-semantics-{suffix}",
        raw_data={},
        # 尚未处理：生命周期未定（NULL），阶段待分类（0066 起队列只认 processing_state）。
        validity_state=None,
        processing_state=ProcessingState.PENDING_CLASSIFICATION,
        validity_reason={
            "code": "pending_classification",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


def _object_result() -> SignalAnalysisResult:
    return SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="high",
        organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        locations=[],
        affected_activities=["production"],
        affected_products=[],
        start_at=datetime(2026, 10, 10, 8, tzinfo=UTC),
        summary_zh="港口管制影响测试供应商",
        evidence_sentences=["测试供应商有限公司受港口管制影响。"],
        confidence=0.9,
    )


def _object_analysis(session: Session, signal: RawSignal) -> AIAnalysisRecord:
    """成功且带真实 JSON object 结果的分析记录（可复用）。"""
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="openai-compatible",
        model="object-v1",
        prompt_version="object-v1",
        status="succeeded",
        finished_at=NOW_UTC,
        duration_ms=1,
        result=_object_result().model_dump(mode="json"),
        needs_review=False,
    )
    session.add(record)
    session.flush()
    return record


def _json_null_filtered_record(session: Session, signal: RawSignal) -> AIAnalysisRecord:
    """ORM 显式 result=None → JSON null（真实 deterministic-filter 落库形态）。"""
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="deterministic-filter",
        model="relevance-v1",
        prompt_version="relevance-v1",
        status="succeeded",
        finished_at=NOW_UTC,
        duration_ms=0,
        result=None,
        error="filtered: 不相关",
        needs_review=True,
        review_reason="预过滤结果待复核：不相关",
    )
    session.add(record)
    session.flush()
    return record


def _sql_null_filtered_record(session: Session, signal: RawSignal) -> None:
    """Core ``null()`` 绕过 JSONB 绑定处理 → 真正的 SQL NULL 过滤记录。"""
    session.execute(
        insert(AIAnalysisRecord).values(
            signal_id=signal.id,
            provider="deterministic-filter",
            model="entity-list-v1",
            prompt_version="entity-list-v1",
            status="succeeded",
            finished_at=NOW_UTC,
            duration_ms=0,
            result=null(),
            error="filtered: SQL NULL",
            needs_review=True,
            review_reason="SQL NULL 过滤记录",
        )
    )
    session.flush()


def _candidate_ids(session: Session) -> list[int]:
    return [
        signal_id
        for signal_id, _ in session.execute(
            scheduler_jobs.pending_signal_candidate_select(now_utc=NOW_UTC)
        )
    ]


def test_empty_result_records_are_not_reusable_and_do_not_block_queue(
    db_session: Session,
) -> None:
    """空结果记录不可复用；队列排除由 processing_state 表达，而非空结果子查询。

    0066 起候选谓词不再内联「终态过滤记录」子查询：写入侧过滤必须落
    ``processing_state='filtered'``。这里同时锁定两种落库形态（JSON null 与 SQL NULL）
    既不被当作可复用分析，也不会因为残留空结果记录而长期占住队列。
    """
    # Given：两条待处理信号各自带 JSON null / SQL NULL 的过滤记录。
    json_source = _source(db_session, "null-json-filter")
    json_signal = _signal(db_session, json_source, "json-null", NOW_UTC - timedelta(hours=1))
    _json_null_filtered_record(db_session, json_signal)
    sql_source = _source(db_session, "null-sql-filter")
    sql_signal = _signal(db_session, sql_source, "sql-null", NOW_UTC - timedelta(hours=1))
    _sql_null_filtered_record(db_session, sql_signal)
    db_session.flush()

    # Then：两种形态都确认落库正确（JSON null 是 SQL 非 NULL，SQL NULL 为真 NULL）。
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(
                AIAnalysisRecord.signal_id == json_signal.id,
                AIAnalysisRecord.result.is_not(None),
            )
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(
                AIAnalysisRecord.signal_id == sql_signal.id,
                AIAnalysisRecord.result.is_(None),
            )
        )
        == 1
    )
    # Then：空结果记录不可复用。
    for signal in (json_signal, sql_signal):
        assert ai_service.latest_reusable_analysis(db_session, signal.id) is None
    # Then：阶段仍是待分类时它们是候选（不再依赖空结果子查询做排除）……
    candidates = _candidate_ids(db_session)
    assert {json_signal.id, sql_signal.id} <= set(candidates)
    # ……写入侧把过滤结论落到 processing_state 后立刻移出队列。
    for signal in (json_signal, sql_signal):
        signal.processing_state = ProcessingState.FILTERED
    db_session.flush()
    candidates = _candidate_ids(db_session)
    assert json_signal.id not in candidates
    assert sql_signal.id not in candidates


def test_pending_candidates_keep_object_analysis_result(db_session: Session) -> None:
    # Given：信号已有真实 JSON object 结果的成功分析
    source = _source(db_session, "object-keep")
    signal = _signal(db_session, source, "object", NOW_UTC - timedelta(hours=1))
    _object_analysis(db_session, signal)

    # When：读取待处理候选
    candidates = _candidate_ids(db_session)

    # Then：仍作为候选，供复用执行全链路
    assert signal.id in candidates


def test_latest_reusable_analysis_prefers_object_over_json_null_filtered(
    db_session: Session,
) -> None:
    # Given：最新成功记录是 JSON null 过滤记录，另一条较早记录是真实对象结果
    source = _source(db_session, "reuse-prefer-object")
    signal = _signal(db_session, source, "reuse", NOW_UTC - timedelta(hours=1))
    _object_analysis(db_session, signal)
    _json_null_filtered_record(db_session, signal)

    # When：读取最近可复用分析
    reusable = ai_service.latest_reusable_analysis(db_session, signal.id)

    # Then：取到对象结果记录，绝不把 JSON null 过滤记录当作可复用
    assert reusable is not None
    assert reusable.provider == "openai-compatible"
    assert reusable.result is not None


def test_head_json_null_filtered_records_do_not_block_later_signal(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：队首两条是已落 filtered 阶段的过滤信号，之后一条是可复用对象结果
    source = _source(db_session, "null-head-block")
    head_old = _signal(db_session, source, "head-old", NOW_UTC - timedelta(hours=3))
    head_new = _signal(db_session, source, "head-new", NOW_UTC - timedelta(hours=2))
    good = _signal(db_session, source, "good", NOW_UTC - timedelta(hours=1))
    _json_null_filtered_record(db_session, head_old)
    _json_null_filtered_record(db_session, head_new)
    # 写入侧过滤结论：确定性过滤落 filtered 后不再回到队列（0066 起由阶段表达）。
    head_old.processing_state = ProcessingState.FILTERED
    head_new.processing_state = ProcessingState.FILTERED
    _object_analysis(db_session, good)
    db_session.commit()

    async def _forbidden(*_args: object, **_kwargs: object) -> AIAnalysisRecord:
        raise AssertionError("不应调用 AI：应复用已有对象结果")

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)
    monkeypatch.setattr(scheduler_jobs, "analyze_raw_signal", _forbidden)

    # When：batch=2，队首过滤信号若未被排除会占满整批
    processed = scheduler_jobs._process_pending_signals(limit=2, now_utc=NOW_UTC)

    # Then：后续信号仍被处理，队首过滤信号未被再处理
    assert processed == 1
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(RiskEventSignal)
            .where(RiskEventSignal.signal_id == good.id)
        )
        == 1
    )
    for head in (head_old, head_new):
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(AIAnalysisRecord)
                .where(AIAnalysisRecord.signal_id == head.id)
            )
            == 1
        )


def test_manual_process_ignores_json_null_filtered_record(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：待处理信号仅有 JSON null 过滤记录（无真实对象结果可复用）。
    # 手工端点使用真实时钟，故信号锚定到真实过去（避免发布时刻晚于当前 UTC 的时段抖动）。
    source = _source(db_session, "null-manual-process")
    signal = _signal(
        db_session, source, "manual", datetime.now(UTC) - timedelta(days=1)
    )
    _json_null_filtered_record(db_session, signal)
    db_session.commit()

    # 注入可判定细类的确定性 Provider：默认 Fake 返回 other/无细类会被判分类失败，
    # 与本用例要验证的「不静默复用过滤记录」无关，避免测试受无关分类路径干扰。
    provider = FakeAIProvider(result=_object_result())
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)

    # When：手工触发完整处理
    response = client.post(f"/api/v1/signals/{signal.id}/process")

    # Then：过滤记录未被当作可复用成功分析（否则 500），改走全新 AI 分析并返回 200
    assert response.status_code == 200
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(
                AIAnalysisRecord.signal_id == signal.id,
                AIAnalysisRecord.provider == "fake",
            )
        )
        == 1
    )
