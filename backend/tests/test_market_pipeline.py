"""确定性行情评估接入调度链（Todo 10）。

锁定的行为契约（每条都对应一种静默失效）：
1. **已登记指标 → 确定性评估，零 LLM**：provider 恒为 ``deterministic-market``，
   任何 ``provider != "deterministic-market"`` 的分析记录都是回归（行情分支一旦
   回落到 AI，这层断言会先炸）。
2. **触发 → 写确定性结果并继续既有处理链**：产出 ``market`` /
   ``commodity_price_move`` 事件，``start_at`` 取 episode 起点，并给关键原材料
   为铜的电缆供应商生成弱关联等级上限内的提醒（含 industry 证据）。
3. **未触发 → 终态过滤**：写空结果的 succeeded 记录 + ``processing_state='no_trigger'``
   处理阶段（生命周期保持不变），不产出任何事件，且后续批次不再被队列谓词捞取
   （否则每日重复统计、重复写记录）。
4. **未命中即回落**：未登记品种 / 配置停用 / ``source_extra`` 缺失或不可解析一律
   走既有路径（commodity 仍由旧分级兜底），既不报错也不静默吞掉信号。
5. **脏观察点只跳过该行**：注册键命中但缺现值时判"无可用观察点"，而不是把整条
   序列算崩或把信号卡在 pending。
6. **同一 episode 第二日刷新快照（Todo 11）**：episode 未断档时复用同一事件行，
   但 summary / severity / facts / 有效期坐标必须刷新为当日口径，``dedup_key``
   不动；提醒仍是同一行 current 并按新严重性更新等级与分数。相邻两日若不复用，
   事件快照会永远停在首日（事件说 medium、提醒已按 critical 计分），属静默失效。
   非行情事件的既有语义不受影响：同键复用仍保留首次快照，画像 identity_override
   路径仍照旧刷新。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.scheduler.jobs as scheduler_jobs
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import OrganizationReference, SignalAnalysisResult
from app.market.config_store import MARKET_CONFIG_KEY, invalidate_market_config_cache
from app.market.evaluation import DETERMINISTIC_CONFIDENCE, DETERMINISTIC_PROVIDER
from app.risks.engine.event_identity import find_or_create_event, refresh_result_fields
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    RuleDimensionConfig,
    SupplierEventMatch,
)
from app.risks.schemas import RiskProcessResult
from app.risks.scoring import compute_level, load_scoring_settings
from app.risks.service import process_analysis
from app.signals.models import DataSource, RawSignal
from app.signals.relevance import invalidate_filter_rules_cache
from app.signals.validity import ProcessingState
from app.suppliers.models import Supplier

NOW: Final = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
CU_SYMBOL: Final = "nf_CU0"
_TEST_VALIDITY_POLICY: Final = {"mode": "fixed_days", "fixed_days": 3650}


def _day(offset: int) -> datetime:
    """NOW 相对第 offset 天的 UTC 零点。"""
    return (NOW + timedelta(days=offset)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _source(session: Session, code: str) -> DataSource:
    """复用迁移已播种的信源，缺失时才新建并启用。"""
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
    source.validity_policy = dict(_TEST_VALIDITY_POLICY)
    session.flush()
    return source


def _extra(value: float, change: float | None = 0.5) -> dict[str, object]:
    extra: dict[str, object] = {"symbol": CU_SYMBOL, "last_price": value}
    if change is not None:
        extra["change_pct"] = change
    return extra


def _history(
    session: Session,
    source: DataSource,
    *,
    index: int,
    collected_at: datetime,
    extra: object,
) -> RawSignal:
    """历史观察点：已被替代的 superseded 态，只参与窗口统计，不是调度候选。"""
    signal = RawSignal(
        source_id=source.id,
        external_id=f"market-hist-{index}",
        dedup_key=f"market-pipeline-hist-{index}",
        title=f"沪铜主力 日行情 {index}",
        content=f"沪铜主力 nf_CU0 收盘 {collected_at.date()}",
        published_at=collected_at,
        collected_at=collected_at,
        fingerprint=f"market-pipeline-hist-fp-{index}",
        raw_data={"title": f"沪铜主力 日行情 {index}", "source_extra": extra},
        validity_key=f"fut-{CU_SYMBOL}",
        validity_profile="market_price_point",
        validity_state="superseded",
        validity_mode="fixed_days",
        valid_from=collected_at,
        valid_until=collected_at + timedelta(days=3650),
        validity_policy_version="market-eval-v1",
        validity_reason={
            "code": "superseded_by_newer_reading",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


def _pending(
    session: Session,
    source: DataSource,
    *,
    index: int,
    collected_at: datetime,
    extra: object,
    title: str = "沪铜主力 日行情",
    content: str = "沪铜主力 nf_CU0 收盘 75600 元/吨，较昨结算：+0.53%。",
) -> RawSignal:
    """本轮候选信号：待分类（生命周期未定），会被调度取出处理。"""
    payload: dict[str, object] = {"title": title, "content": content}
    if extra is not None:
        payload["source_extra"] = extra
    signal = RawSignal(
        source_id=source.id,
        external_id=f"market-pending-{index}",
        dedup_key=f"market-pipeline-pending-{index}",
        title=title,
        content=content,
        published_at=collected_at,
        collected_at=collected_at,
        fingerprint=f"market-pipeline-pending-fp-{index}",
        raw_data=payload,
        validity_key=f"fut-{CU_SYMBOL}",
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


def _seed_series(
    session: Session,
    source: DataSource,
    *,
    index: int,
    first: float,
    last: float,
    count: int,
    change: float | None,
) -> RawSignal:
    """种 count 个等间隔历史观察点，返回最后一个（待处理候选）。"""
    step = (last - first) / (count - 1) if count > 1 else 0.0
    for offset_index in range(count - 1):
        _history(
            session,
            source,
            index=offset_index,
            collected_at=_day(-count + 1 + offset_index),
            extra=_extra(first + step * offset_index, change),
        )
    return _pending(
        session,
        source,
        index=index,
        collected_at=_day(0),
        extra=_extra(last, change),
    )


def _cable_supplier(session: Session) -> Supplier:
    supplier = Supplier(
        supplier_code="MKT-CU-0001",
        legal_name="沪上电缆供应商",
        country_code="CN",
        registry_no="91310000TESTCABLE",
        raw_materials=["铜"],
    )
    session.add(supplier)
    session.flush()
    return supplier


def _analyses(session: Session, signal: RawSignal) -> list[AIAnalysisRecord]:
    return list(
        session.scalars(
            select(AIAnalysisRecord).where(AIAnalysisRecord.signal_id == signal.id)
        )
    )


@pytest.fixture
def no_llm(monkeypatch: MonkeyPatch) -> None:
    """行情分支一旦回落到 AI 立即炸：任何 LLM 调用都是回归。"""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("行情分支不得调用 AI Provider")

    monkeypatch.setattr(scheduler_jobs, "analyze_raw_signal", _boom)


@pytest.fixture(autouse=True)
def _market_analysis_enabled(db_session: Session) -> Iterator[None]:
    """本文件锁定"判定总开关开启"时的既有链路契约。

    生产默认已改为关闭（只记录）；这里的用例验证关闭后仍可用的历史行为，
    因此显式写入 ``analysis_enabled=true``，默认值另有用例覆盖。
    """
    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
    )
    if row is None:
        db_session.add(
            RuleDimensionConfig(
                key=MARKET_CONFIG_KEY,
                label="行情指标运行配置",
                enabled=True,
                config={"analysis_enabled": True},
            )
        )
    else:
        row.enabled = True
        row.config = {"analysis_enabled": True}
    db_session.commit()
    invalidate_market_config_cache()
    yield
    invalidate_market_config_cache()


@pytest.fixture(autouse=True)
def _isolated_rule_caches() -> Iterator[None]:
    """隔离进程级 TTL 缓存，避免跨测试文件串味。

    ``load_filter_rules`` / ``load_market_config`` 都使用 60 秒进程级缓存；
    其他测试文件（如 ``test_filter_config``）可能留下阈值 1.0 等覆盖，导致本文件的
    "回落旧分级" 用例误走 AI 路径。每个用例前后各失效一次，lock 住真实契约。
    """
    invalidate_filter_rules_cache()
    invalidate_market_config_cache()
    try:
        yield
    finally:
        invalidate_filter_rules_cache()
        invalidate_market_config_cache()


# ---------------------------------------------------------------------------
# 一、触发：确定性评估 → 事件 → 提醒（零 LLM）
# ---------------------------------------------------------------------------


def test_triggered_market_signal_produces_event_and_alert_without_llm(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：20 点沪铜从 70000 涨到 75600（累计恰好 +8.0%），单日仅 0.5%
    source = _source(db_session, "commodity-futures")
    supplier = _cable_supplier(db_session)
    signal = _seed_series(
        db_session, source, index=1, first=70000.0, last=75600.0,
        count=20, change=0.5,
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", True)

    # When：调度器处理待处理信号。
    processed = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW)

    # Then：确定性记录 + market 事件 + 电缆供应商提醒，全程无 LLM 记录。
    assert processed == 1
    db_session.refresh(signal)
    records = _analyses(db_session, signal)
    assert len(records) == 1
    analysis = records[0]
    assert analysis.provider == DETERMINISTIC_PROVIDER
    assert analysis.model == "market-eval-v1"
    assert analysis.prompt_version == "market-eval-v1"
    assert analysis.status == "succeeded"
    assert analysis.needs_review is False
    assert analysis.result is not None
    assert analysis.result["event_type"] == "market"
    assert analysis.result["event_subtype"] == "commodity_price_move"
    assert analysis.result["suggested_level"] is None
    # 全库没有任何非确定性 provider 的分析记录（Fake/OpenAI 都不允许出现）
    assert {
        row[0]
        for row in db_session.execute(select(AIAnalysisRecord.provider).distinct())
    } == {DETERMINISTIC_PROVIDER}

    event = db_session.scalar(select(RiskEvent))
    assert event is not None
    assert event.event_type == "market"
    assert event.event_subtype == "commodity_price_move"
    # episode 起点：无历史触发记录时取当前日期 UTC 00:00
    assert event.start_at == _day(0)
    assert db_session.scalar(
        select(func.count())
        .select_from(RiskEventSignal)
        .where(RiskEventSignal.signal_id == signal.id)
    ) == 1

    # 电缆供应商（关键原材料=铜）命中行业柱，提醒落在弱关联等级上限内
    match = db_session.scalar(
        select(SupplierEventMatch).where(SupplierEventMatch.supplier_id == supplier.id)
    )
    assert match is not None
    assert match.match_type == "industry"
    assert any(item.get("object_type") == "industry" for item in match.evidence)
    alert = db_session.scalar(
        select(RiskAlert).where(RiskAlert.match_id == match.id)
    )
    assert alert is not None
    assert alert.level != "P1"
    assert alert.level in {"P2", "P3", "P4"}
    assert alert.status == "current"
    assert alert.score_detail["dimension"] == "industry"


def test_single_shock_reading_is_enough_to_trigger(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：最小种子——单条带 change_pct=+3.5% 的沪铜读数（冲击线 3.0%）
    source = _source(db_session, "commodity-futures")
    signal = _pending(
        db_session,
        source,
        index=200,
        collected_at=_day(0),
        extra=_extra(70000.0, 3.5),
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))

    # When / Then：单点即触发，不因观察点不足而静默。
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 1
    db_session.refresh(signal)
    analysis = _analyses(db_session, signal)[0]
    assert analysis.provider == DETERMINISTIC_PROVIDER
    assert analysis.result is not None
    assert "冲击条件" in analysis.result["evidence_sentences"][-1]
    event = db_session.scalar(select(RiskEvent))
    assert event is not None
    assert event.event_subtype == "commodity_price_move"


# ---------------------------------------------------------------------------
# 二、未触发：终态过滤 + 不可重复取出
# ---------------------------------------------------------------------------


def test_untriggered_market_signal_is_terminally_filtered(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：20 点沪铜在 70000→70100 内小幅波动（累计 0.14%，单日 0.4%）
    source = _source(db_session, "commodity-futures")
    signal = _seed_series(
        db_session, source, index=2, first=70000.0, last=70100.0,
        count=20, change=0.4,
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))

    # When：调度器处理待处理信号。
    processed = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW)

    # Then：终态过滤记录（result 为空、无需人工复核）+ no_trigger 处理阶段 + 无事件。
    assert processed == 0
    db_session.refresh(signal)
    analysis = _analyses(db_session, signal)[0]
    assert analysis.provider == DETERMINISTIC_PROVIDER
    assert analysis.model == "market-eval-v1"
    assert analysis.status == "succeeded"
    assert analysis.result is None
    assert analysis.needs_review is False
    assert analysis.error is not None and "below_threshold" in analysis.error
    # 行情未触发只改处理阶段，生命周期仍为未定（NULL）。
    assert signal.processing_state == ProcessingState.NO_TRIGGER
    assert signal.validity_state is None
    assert signal.validity_reason["code"] == "filtered_out"
    assert signal.validity_reason["details"]["filter"] == scheduler_jobs.MARKET_FILTER_KIND
    assert "未达触发线" in signal.validity_reason["details"]["reason"]
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0


def test_untriggered_market_signal_is_not_reprocessed_in_next_round(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：第一轮已把低波动读数写成终态过滤。
    source = _source(db_session, "commodity-futures")
    signal = _seed_series(
        db_session, source, index=3, first=70000.0, last=70100.0,
        count=20, change=0.4,
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 0

    # When：下一轮再跑一次（stale_state 对抗：不得重复取出、重复写记录）。
    db_session.refresh(signal)
    second = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW)

    # Then
    assert second == 0
    db_session.refresh(signal)
    assert signal.processing_state == ProcessingState.NO_TRIGGER
    assert len(_analyses(db_session, signal)) == 1


def test_market_signal_without_usable_value_is_filtered_as_no_observations(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：注册键命中但缺现值（脏观察点）——只跳过该行，序列为空
    source = _source(db_session, "commodity-futures")
    signal = _pending(
        db_session, source, index=300, collected_at=_day(0),
        extra={"symbol": CU_SYMBOL},
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))

    # When / Then：判为"无可用观察点"终态过滤，而不是失败或静默滞留。
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 0
    db_session.refresh(signal)
    analysis = _analyses(db_session, signal)[0]
    assert analysis.provider == DETERMINISTIC_PROVIDER
    assert analysis.result is None
    assert analysis.error is not None and "no_observations" in analysis.error
    assert signal.processing_state == ProcessingState.NO_TRIGGER
    assert signal.validity_state is None


# ---------------------------------------------------------------------------
# 三、未命中回落既有路径（不得改既有过滤链顺序与非行情信源行为）
# ---------------------------------------------------------------------------


def test_unregistered_symbol_falls_back_to_legacy_grading(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：commodity-futures 的未登记品种 + 低波动正文（旧分级兜底可解析）
    source = _source(db_session, "commodity-futures")
    signal = _pending(
        db_session,
        source,
        index=400,
        collected_at=_day(0),
        extra={"symbol": "nf_XX0", "last_price": 12000.0, "change_pct": -1.2},
        title="新品种日行情",
        content="主力合约收盘 12000 元/吨，较昨结算：-1.20%。",
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    # When / Then：未被行情分支接管，回落旧结构化分级并过滤。
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 0
    db_session.refresh(signal)
    analysis = _analyses(db_session, signal)[0]
    assert analysis.provider == "deterministic-filter"
    assert analysis.model == "structured-grade-v1"
    assert signal.validity_reason["details"]["filter"] == (
        scheduler_jobs.STRUCTURED_GRADE_FILTER_KIND
    )
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0


@pytest.mark.parametrize(
    ("suffix", "extra"),
    [
        ("unparseable", "{not-json"),
        ("not-mapping", 42),
        ("empty-mapping", {}),
        ("missing-key", None),
    ],
)
def test_unparseable_source_extra_falls_back_without_error(
    db_session: Session,
    monkeypatch: MonkeyPatch,
    no_llm: None,
    suffix: str,
    extra: object,
) -> None:
    # Given：source_extra 不可解析 / 缺键——行情分支必须静默回落而不是报错
    source = _source(db_session, "commodity-futures")
    signal = _pending(
        db_session,
        source,
        index=500 + len(suffix),
        collected_at=_day(0),
        extra=extra,
        title="异常行情读数",
        content="主力合约收盘 78000 元/吨，较昨结算：-1.20%。",
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)

    # When / Then：不抛错，回落旧分级过滤（不是行情终态）。
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 0
    db_session.refresh(signal)
    analysis = _analyses(db_session, signal)[0]
    assert analysis.provider == "deterministic-filter"
    assert signal.validity_reason["details"]["filter"] == (
        scheduler_jobs.STRUCTURED_GRADE_FILTER_KIND
    )


def test_disabled_indicator_falls_back_to_legacy_path(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    # Given：运行期把 fut-CU 停用（视同未登记）
    invalidate_market_config_cache()
    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
    )
    assert row is not None
    row.config = {"indicators": {"fut-CU": {"enabled": False}}}
    db_session.commit()
    source = _source(db_session, "commodity-futures")
    signal = _pending(
        db_session,
        source,
        index=600,
        collected_at=_day(0),
        extra=_extra(78000.0, -1.2),
        content="沪铜主力 nf_CU0 收盘 78000 元/吨，较昨结算：-1.20%。",
    )
    db_session.commit()
    invalidate_market_config_cache()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", False)
    try:
        # When / Then：停用指标不被行情分支接管，回落旧分级。
        assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 0
        db_session.refresh(signal)
        analysis = _analyses(db_session, signal)[0]
        assert analysis.provider == "deterministic-filter"
        assert signal.validity_reason["details"]["filter"] == (
            scheduler_jobs.STRUCTURED_GRADE_FILTER_KIND
        )
    finally:
        invalidate_market_config_cache()


# ---------------------------------------------------------------------------
# 四、同一 episode 第二日：复用事件行 + 刷新快照（Todo 11）
# ---------------------------------------------------------------------------


def test_second_day_same_episode_refreshes_event_snapshot_in_place(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    """第二日再次触发时复用同一事件，只把快照刷新成当日口径（dedup_key 不动）。"""
    # Given：第一日 20 点沪铜 70000→75600，累计恰好 +8.00% 触持续线，落 medium 档。
    source = _source(db_session, "commodity-futures")
    supplier = _cable_supplier(db_session)
    _seed_series(
        db_session, source, index=700, first=70000.0, last=75600.0,
        count=20, change=0.5,
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))

    # When：第一日调度处理。
    assert scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW) == 1

    # Then：首日事件与提醒。
    first_event = db_session.scalar(select(RiskEvent))
    assert first_event is not None
    assert first_event.severity == "medium"
    assert "+8.00%" in first_event.summary
    first_match = db_session.scalar(
        select(SupplierEventMatch).where(SupplierEventMatch.supplier_id == supplier.id)
    )
    assert first_match is not None
    first_alert = db_session.scalar(
        select(RiskAlert).where(RiskAlert.match_id == first_match.id)
    )
    assert first_alert is not None
    assert first_alert.status == "current"
    event_id = first_event.id
    dedup_key = first_event.dedup_key
    day1_summary = first_event.summary
    day1_facts = dict(first_event.facts or {})
    day1_alert_id = first_alert.id
    day1_alert_level = first_alert.level
    day1_alert_score = first_alert.score

    # Given：第二日同一 episode 再触发——沪铜跳涨到 90000（累计 +28.57% → critical）。
    _pending(
        db_session,
        source,
        index=701,
        collected_at=_day(1),
        extra=_extra(90000.0, 19.05),
        content="沪铜主力 nf_CU0 收盘 90000 元/吨，较昨结算：+19.05%。",
    )
    db_session.commit()

    # When：第二日调度处理。
    assert scheduler_jobs._process_pending_signals(
        limit=20, now_utc=NOW + timedelta(days=1)
    ) == 1

    # Then：同一事件行（不新建、不换键），快照已刷新为第二日口径。
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 1
    event = db_session.get(RiskEvent, event_id)
    assert event is not None
    assert event.dedup_key == dedup_key
    assert event.severity == "critical"
    assert event.summary != day1_summary
    assert "+28.57%" in event.summary
    assert "+8.00%" not in event.summary
    # 有效期坐标：start_at 仍为 episode 起点（不随日滚动），end_at 保持为空。
    assert event.start_at == _day(0)
    assert event.end_at is None
    # facts 同步刷新为第二日分析结果（含当日现值）。
    assert event.facts is not None
    assert event.facts["suggested_severity"] == "critical"
    assert event.facts["summary_zh"] == event.summary
    assert event.facts["confidence"] == DETERMINISTIC_CONFIDENCE
    assert "变为 90000" in event.facts["evidence_sentences"][1]
    assert event.facts != day1_facts

    # Then：提醒仍是同一行 current，等级与分数按 critical 重算。
    match = db_session.scalar(
        select(SupplierEventMatch).where(SupplierEventMatch.event_id == event_id)
    )
    assert match is not None and match.id == first_match.id
    alerts = list(
        db_session.scalars(select(RiskAlert).where(RiskAlert.match_id == match.id))
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.id == day1_alert_id
    assert alert.status == "current"
    settings = load_scoring_settings()
    severity_delta = (
        settings.severity_scores["critical"] - settings.severity_scores["medium"]
    )
    assert alert.score == day1_alert_score + severity_delta
    assert alert.level != day1_alert_level
    assert alert.level == compute_level(settings, alert.score)
    assert alert.score_detail["severity"] == settings.severity_scores["critical"]
    assert alert.score_detail["final_level"] == alert.level


def _non_market_result(
    *,
    summary: str,
    severity: str,
    organization: OrganizationReference,
) -> SignalAnalysisResult:
    """同主体、同日的非行情事件分析结果：dedup_key 只由主体与日期决定。"""
    return SignalAnalysisResult(
        event_type="judicial",
        event_subtype="judicial_case",
        suggested_severity=severity,
        organizations=[organization],
        locations=[],
        affected_activities=["judicial"],
        affected_products=[],
        start_at=_day(0),
        end_at=None,
        summary_zh=summary,
        evidence_sentences=["法院公告显示存在未决案件。"],
        confidence=0.7,
    )


def _analyze(
    session: Session,
    signal: RawSignal,
    result: SignalAnalysisResult,
    *,
    now: datetime,
) -> RiskProcessResult:
    """绕过 AI Provider 直接喂一条静态分析结果，走既有处理链。"""
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="static-test",
        model="static-v1",
        prompt_version="static-v1",
        status="succeeded",
        started_at=signal.collected_at,
        finished_at=signal.collected_at,
        result=result.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return process_analysis(session, signal, record, now_utc=now)


def test_non_market_event_keeps_first_snapshot_on_same_key_reuse(
    db_session: Session,
) -> None:
    """非行情事件同键复用保留首次快照（不得被行情刷新语义带跑）。"""
    # Given：同主体同日的两条司法事件（dedup_key 相同、摘要与严重性不同）。
    source = _source(db_session, "commodity-futures")
    organization = OrganizationReference(
        name="沪上电缆供应商", registry_no="91310000TESTCABLE"
    )
    first = _pending(
        db_session, source, index=800, collected_at=_day(0), extra=None,
        title="司法进展公告", content="某供应商涉诉公告首次披露。",
    )
    db_session.commit()
    outcome_1 = _analyze(
        db_session,
        first,
        _non_market_result(
            summary="首次司法摘要", severity="medium", organization=organization
        ),
        now=NOW,
    )

    # When：同日第二则公告落库并处理。
    second = _pending(
        db_session, source, index=801, collected_at=_day(0), extra=None,
        title="司法进展公告二则", content="同主体同日第二则司法进展公告。",
    )
    db_session.commit()
    outcome_2 = _analyze(
        db_session,
        second,
        _non_market_result(
            summary="第二次司法摘要", severity="critical", organization=organization
        ),
        now=NOW,
    )

    # Then：复用同一事件，且快照保持首次口径（既有语义未被行情刷新改写）。
    assert outcome_2.event_created is False
    assert outcome_2.event_id == outcome_1.event_id
    event = db_session.get(RiskEvent, outcome_1.event_id)
    assert event is not None
    assert event.summary == "首次司法摘要"
    assert event.severity == "medium"


def test_analysis_disabled_only_records_without_events_or_alerts(
    db_session: Session, monkeypatch: MonkeyPatch, no_llm: None
) -> None:
    """判定总开关关闭（生产默认）：只落记录、终结信号，不判涨跌幅、不产事件/提醒。"""
    # Given：序列已满足持续触发条件（20 点累计 +8%），开关关闭时也不得判定。
    source = _source(db_session, "commodity-futures")
    signal = _seed_series(
        db_session, source, index=1, first=70000.0, last=75600.0,
        count=20, change=0.5,
    )
    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
    )
    assert row is not None
    row.config = {"analysis_enabled": False}
    db_session.commit()
    invalidate_market_config_cache()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "SIGNAL_RELEVANCE_FILTER_ENABLED", True)

    # When
    processed = scheduler_jobs._process_pending_signals(limit=20, now_utc=NOW)

    # Then：终态 + 仅记录原因，且全库没有任何事件或提醒。
    assert processed == 0
    db_session.refresh(signal)
    assert signal.processing_state == ProcessingState.NO_TRIGGER
    assert (
        signal.validity_reason["details"]["filter"] == scheduler_jobs.MARKET_RECORD_KIND
    )
    records = _analyses(db_session, signal)
    assert len(records) == 1
    assert records[0].result is None
    assert records[0].error is not None and records[0].error.startswith("recorded:")
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0


def test_profile_override_still_refreshes_snapshot_and_keeps_key(
    db_session: Session,
) -> None:
    """画像 identity_override 路径保持跨周刷新，且公开的 refresh_result_fields 同语义。"""
    organization = OrganizationReference(
        name="沪上电缆供应商", registry_no="91310000TESTCABLE"
    )
    week1 = _non_market_result(
        summary="画像第一周摘要", severity="medium", organization=organization
    )
    override = "supplier_profile:MKT-CU-0001"

    event, created = find_or_create_event(db_session, week1, identity_override=override)
    db_session.commit()
    assert created is True
    dedup_key = event.dedup_key

    week2 = _non_market_result(
        summary="画像第二周摘要", severity="critical", organization=organization
    )
    reused, created_2 = find_or_create_event(
        db_session, week2, identity_override=override
    )
    db_session.commit()

    # 画像路径：同键同行，但快照已刷新为第二周口径。
    assert created_2 is False
    assert reused.id == event.id
    assert reused.dedup_key == dedup_key
    assert reused.summary == "画像第二周摘要"
    assert reused.severity == "critical"

    # 公开的 refresh_result_fields 与画像路径刷新完全同语义（键与有效期坐标不动）。
    refresh_result_fields(reused, week1)
    assert reused.summary == "画像第一周摘要"
    assert reused.severity == "medium"
    assert reused.dedup_key == dedup_key
    assert reused.start_at == _day(0)