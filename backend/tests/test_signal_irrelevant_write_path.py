"""确定性预筛过滤分支只改处理阶段的写路径锁定。

三个过滤分支（相关性预筛 / 清单未命中 / 结构化分级）此前只写 filtered 分析记录，
信号本身长期滞留 pending_classification；本文件锁定：过滤发生后信号进入
``processing_state='filtered'`` 并带 ``filtered_out`` 原因，**生命周期
（validity_state）保持原值不被覆盖**，不再生成风险事件，也不再被分析队列谓词捞取。
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.scheduler.jobs as scheduler_jobs
from app.ai.models import AIAnalysisRecord
from app.risks.models import RiskEvent, RiskEventSignal
from app.risks.query_validity import valid_signal_condition
from app.risks.validity import is_raw_signal_effective
from app.signals.models import DataSource, RawSignal
from app.signals.relevance import invalidate_filter_rules_cache, load_filter_rules
from app.signals.validity import ProcessingState, ValidityState

NOW_UTC = datetime(2026, 10, 12, 12, tzinfo=UTC)


def _source(session: Session, code: str) -> DataSource:
    """复用迁移已播种的信源（ofac-sdn / commodity-futures 等），缺失时才新建并启用。"""
    source = session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
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
    source.enabled = True
    session.flush()
    return source


def _signal(
    session: Session,
    source: DataSource,
    suffix: str,
    *,
    title: str,
    content: str,
    published_at: datetime | None = NOW_UTC - timedelta(hours=1),
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=suffix,
        title=title,
        content=content,
        published_at=published_at,
        collected_at=NOW_UTC,
        fingerprint=f"irrelevant-{suffix}",
        raw_data={},
        validity_state=None,
        processing_state=ProcessingState.PENDING_CLASSIFICATION,
        validity_reason={
            "code": "pending_classification",
            "anchor_source": "published_at" if published_at else "collected_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


def _assert_irrelevant_and_excluded(
    session: Session, signal: RawSignal, source: DataSource
) -> None:
    """处理阶段 filtered + filtered_out 原因 + 无事件 + 队列与判活谓词双排除。"""
    assert signal.processing_state == ProcessingState.FILTERED
    # 过滤结论不是生命周期：未被策略解析的信号生命周期仍为未定（NULL）。
    assert signal.validity_state is None
    assert signal.validity_reason["code"] == "filtered_out"
    assert signal.validity_reason["details"]["filter"]
    assert signal.validity_reason["details"]["reason"]
    assert signal.validity_reason["anchor_source"] in (
        "published_at",
        "collected_at",
        "official_valid_until",
        "event_end",
        "legacy",
    )
    # 过滤记录仍在（可追溯 + 队列纵深防御），且结果为空。
    analysis = session.scalar(
        select(AIAnalysisRecord).where(AIAnalysisRecord.signal_id == signal.id)
    )
    assert analysis is not None
    assert analysis.status == "succeeded"
    assert analysis.result is None
    # 不生成任何风险事件与信号关联。
    assert session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert session.scalar(select(func.count()).select_from(RiskEventSignal)) == 0
    # 不再进入分析队列（候选谓词只接受形成阶段 pending_classification）。
    assert signal.id not in [
        signal_id
        for signal_id, _ in session.execute(
            scheduler_jobs.pending_signal_candidate_select(now_utc=NOW_UTC)
        )
    ]
    # 不计有效信号（判活谓词为 ACTIVE/LEGACY allow-list）。
    assert signal.id not in session.scalars(
        select(RawSignal.id).where(valid_signal_condition(source, now_utc=NOW_UTC))
    )


def test_relevance_filter_marks_signal_irrelevant(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：仅命中无供应商所在国的国外地理、无高影响关键词的信号。
    source = _source(db_session, "irrelevant-relevance")
    signal = _signal(
        db_session,
        source,
        "relevance",
        title="Branch maintenance notice",
        content="The branch in Germany will be closed for maintenance next week.",
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", True)

    # When：调度器处理待处理信号。
    processed = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC)

    # Then：判定不相关、进入 irrelevant 终态、不产出事件、不再入队。
    assert processed == 0
    db_session.refresh(signal)
    _assert_irrelevant_and_excluded(db_session, signal, source)
    assert signal.validity_reason["details"]["filter"] == scheduler_jobs.RELEVANCE_FILTER_KIND
    assert signal.validity_reason["anchor_source"] == "published_at"


def test_entity_list_filter_marks_signal_irrelevant(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：全量实体清单信源（ofac-sdn）返回未命中任何供应商主体的条目。
    source = _source(db_session, "ofac-sdn")
    signal = _signal(
        db_session,
        source,
        "entity-list",
        title="ACME HOLDINGS LIMITED",
        content="Registration number 0000000000, jurisdiction offshore.",
        published_at=None,
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)
    # Given 前置：清单信源集合默认含 ofac-sdn（绕过 60s TTL 缓存，杜绝跨用例串扰）。
    invalidate_filter_rules_cache()
    assert source.code in load_filter_rules(db_session).list_sources

    # When：调度器处理待处理信号。
    processed = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC)

    # Then：清单未命中同样落 irrelevant，anchor 回落到 collected_at。
    assert processed == 0
    db_session.refresh(signal)
    _assert_irrelevant_and_excluded(db_session, signal, source)
    assert signal.validity_reason["details"]["filter"] == scheduler_jobs.ENTITY_LIST_FILTER_KIND
    assert signal.validity_reason["anchor_source"] == "collected_at"


def test_structured_grade_filter_marks_signal_irrelevant(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：commodity-futures 价格信号涨跌幅低于阈值（正常波动）。
    source = _source(db_session, "commodity-futures")
    signal = _signal(
        db_session,
        source,
        "structured-grade",
        title="铜期货日行情",
        content="主力合约收盘 78000 元/吨，较昨结算：-1.20%。",
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    # When：调度器处理待处理信号。
    processed = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC)

    # Then：结构化分级跳过同样落 irrelevant。
    assert processed == 0
    db_session.refresh(signal)
    _assert_irrelevant_and_excluded(db_session, signal, source)
    assert (
        signal.validity_reason["details"]["filter"]
        == scheduler_jobs.STRUCTURED_GRADE_FILTER_KIND
    )


def test_irrelevant_signal_survives_next_scheduler_round(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given：已落 irrelevant 的信号与一条仍待处理的可解析信号。
    source = _source(db_session, "irrelevant-repeat")
    filtered = _signal(
        db_session,
        source,
        "repeat-filtered",
        title="Branch maintenance notice",
        content="The branch in Germany will be closed for maintenance next week.",
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", True)
    scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC)

    # When：下一轮再跑一次。
    db_session.refresh(filtered)
    second_round = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW_UTC)

    # Then：已过滤信号不会被重复捞取，也不产生第二条过滤记录。
    assert second_round == 0
    db_session.refresh(filtered)
    assert filtered.processing_state == ProcessingState.FILTERED
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AIAnalysisRecord)
            .where(AIAnalysisRecord.signal_id == filtered.id)
        )
        == 1
    )


def test_irrelevant_signal_is_not_effective_for_business_chain(db_session: Session) -> None:
    # Given：已确定性过滤的信号（手动构造，锁定 Python 侧判活入口）。
    source = _source(db_session, "irrelevant-manual")
    signal = _signal(
        db_session,
        source,
        "manual",
        title="Branch maintenance notice",
        content="The branch in Germany will be closed for maintenance next week.",
    )
    signal.processing_state = ProcessingState.FILTERED
    signal.validity_state = ValidityState.IRRELEVANT
    signal.validity_reason = {
        "code": "filtered_out",
        "anchor_source": "published_at",
        "details": {"filter": "relevance", "reason": "事件地理 ['DE'] 均无供应商"},
    }
    db_session.flush()

    # Then：判活入口返回 False（业务处理链拒绝），绝不会被当作有效信号生成事件。
    assert is_raw_signal_effective(signal, now_utc=NOW_UTC) is False