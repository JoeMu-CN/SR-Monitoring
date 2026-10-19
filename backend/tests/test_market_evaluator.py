"""确定性行情评估器（Todo 8）。

锁定最容易静默失效的几件事：
1. 阈值含等于——累计恰好 8.00% / 单日恰好 3.00% 必须触发，7.99% / 2.99% 不触发；
2. ``min_observations`` 只约束持续条件，冲击条件单条读数即可触发（否则单点永远静默）；
3. ``change_key`` 缺失时（汇率口径）由相邻观察点推导单日变化；
4. episode 归并按「两次信号之间的观察点数」而非自然日，跨 ``episode_gap`` 开新 episode；
5. 脏观察点（``source_extra`` 不可解析 / 缺现值 / 属于别的指标）只跳过该行，不让整条序列失败；
6. 被 ``until_superseded`` 替代的读数仍参与窗口——只认 active 会让窗口恒为 1 点且无从察觉。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.market.evaluation import (
    DETERMINISTIC_CONFIDENCE,
    DETERMINISTIC_PROVIDER,
    SERIES_RETENTION_DAYS,
    Observation,
    build_filtered_reason,
    build_result,
    compute_stats,
    evaluate_series,
    load_series,
    resolve_episode_start,
)
from app.market.registry import INDICATORS_BY_CODE, IndicatorSpec
from app.signals.models import DataSource, RawSignal

NOW: Final = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
CU: Final = INDICATORS_BY_CODE["fut-CU"]
CNY: Final = INDICATORS_BY_CODE["fx-CNY"]

# 期货分档：≥15 critical / ≥10 high / ≥5 medium；汇率：≥8 / ≥5 / ≥3。
_MISSING: Final = object()


def _day(offset: int) -> datetime:
    """NOW 相对第 offset 天的 UTC 零点。"""
    return (NOW + timedelta(days=offset)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _rising(
    first: float, last: float, *, count: int, change: float | None = 0.4
) -> tuple[Observation, ...]:
    """count 个观察点，从 first 线性升到 last；change 为原始 change_key 读数。"""
    step = (last - first) / (count - 1) if count > 1 else 0.0
    return tuple(
        Observation(
            signal_id=9_000 + index,
            collected_at=_day(-count + 1 + index),
            value=first + step * index,
            change_pct=change,
        )
        for index in range(count)
    )


def _flat(value: float, *, count: int, change: float | None) -> tuple[Observation, ...]:
    """count 个等值观察点，change 为原始 change_key 读数（None 表示只有现值）。"""
    return tuple(
        Observation(
            signal_id=9_500 + index,
            collected_at=_day(-count + 1 + index),
            value=value,
            change_pct=change,
        )
        for index in range(count)
    )


def _spec(code: str) -> IndicatorSpec:
    return INDICATORS_BY_CODE[code]


# ---------------------------------------------------------------------------
# 一、阈值含等于
# ---------------------------------------------------------------------------


def test_cumulative_change_exactly_at_threshold_triggers() -> None:
    # Given / When：20 点从 100.0 升到 108.0，累计恰好等于期货持续阈值 8.0%
    series = _rising(100.0, 108.0, count=20)
    stats = compute_stats(CU, series)

    # Then
    assert stats.cum_pct == pytest.approx(8.0)
    assert stats.cum_window == 20
    assert stats.base_value == pytest.approx(100.0)
    assert stats.latest_value == pytest.approx(108.0)
    verdict = evaluate_series(CU, series)
    assert verdict.triggered is True
    assert verdict.condition == "sustained"
    assert verdict.severity == "medium"


def test_cumulative_change_just_below_threshold_does_not_trigger() -> None:
    # Given / When：7.99% < 8.0%，单日变化 0.4% 也远低于冲击线 3.0%
    verdict = evaluate_series(CU, _rising(100.0, 107.99, count=20))

    # Then
    assert verdict.triggered is False
    assert compute_stats(CU, _rising(100.0, 107.99, count=20)).cum_pct == pytest.approx(7.99)
    reason = build_filtered_reason(CU, verdict)
    assert reason.code == "below_threshold"


def test_daily_change_exactly_at_threshold_triggers_shock() -> None:
    # Given / When：单日变化恰好等于冲击阈值 3.0%（含等于）
    verdict = evaluate_series(CU, _flat(100.0, count=6, change=3.0))

    # Then
    assert verdict.triggered is True
    assert verdict.condition == "shock"
    assert verdict.stats.max_day_pct == pytest.approx(3.0)
    assert verdict.stats.max_day_change == pytest.approx(3.0)
    # 3.0% 低于期货最低档 5%，按确定性兜底取最低档，绝不返回空严重性
    assert verdict.severity == "medium"


def test_daily_change_just_below_threshold_does_not_trigger() -> None:
    # Given / When：2.99% < 3.0%
    verdict = evaluate_series(CU, _flat(100.0, count=6, change=2.99))

    # Then
    assert verdict.triggered is False
    assert verdict.stats.max_day_pct == pytest.approx(2.99)


def test_both_conditions_trigger_uses_the_larger_magnitude_for_severity() -> None:
    # Given / When：持续 12%（high 档）+ 单日 4%（低于最低档 5%），取越阈较大者
    series = _rising(100.0, 112.0, count=20, change=4.0)
    verdict = evaluate_series(CU, series)

    # Then
    assert verdict.sustained_triggered is True
    assert verdict.shock_triggered is True
    assert verdict.magnitude_pct == pytest.approx(12.0)
    assert verdict.severity == "high"
    assert verdict.condition == "sustained"


# ---------------------------------------------------------------------------
# 二、方向：up / down / both
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "last", "triggered"),
    [
        ("up", 108.0, True),
        ("up", 92.0, False),
        ("down", 108.0, False),
        ("down", 92.0, True),
        ("both", 108.0, True),
        ("both", 92.0, True),
    ],
)
def test_direction_filters_sustained_trigger(
    direction: str, last: float, triggered: bool
) -> None:
    # Given / When
    spec = replace(CU, direction=direction)
    start = 100.0 if last > 100.0 else 108.0
    series = _rising(start, last, count=20)

    # Then
    assert evaluate_series(spec, series).triggered is triggered


def test_shock_direction_follows_indicator_direction() -> None:
    # Given / When：只关心上行的指标遇到单日 -5.0%，不得触发
    up_only = replace(CU, direction="up")

    # Then
    assert evaluate_series(up_only, _flat(100.0, count=6, change=-5.0)).triggered is False
    assert evaluate_series(up_only, _flat(100.0, count=6, change=5.0)).triggered is True


def test_down_only_indicator_ignores_upward_shock() -> None:
    # Given / When
    down_only = replace(CU, direction="down")

    # Then
    assert evaluate_series(down_only, _flat(100.0, count=6, change=5.0)).triggered is False
    assert evaluate_series(down_only, _flat(100.0, count=6, change=-5.0)).triggered is True


# ---------------------------------------------------------------------------
# 三、min_observations 只约束持续条件
# ---------------------------------------------------------------------------


def test_nine_observations_stay_in_accumulating_state() -> None:
    # Given / When：9 点累计已达 8%，但期货 min_observations=10
    verdict = evaluate_series(CU, _rising(100.0, 108.0, count=9))

    # Then：持续条件被最小观察点数挡住
    assert verdict.triggered is False
    assert verdict.stats.cum_pct == pytest.approx(8.0)
    reason = build_filtered_reason(CU, verdict)
    assert reason.code == "accumulating"
    assert "9/10" in reason.detail_zh
    assert "沪铜主力" in reason.detail_zh


def test_shock_is_not_gated_by_min_observations() -> None:
    # Given / When：同样只有 9 个观察点，但存在 4.0% 单日变化
    verdict = evaluate_series(CU, _flat(100.0, count=9, change=4.0))

    # Then：冲击条件不受最小观察点数约束，单点量级的读数也能立刻产生事件
    assert verdict.stats.observations < CU.min_observations
    assert verdict.triggered is True
    assert verdict.condition == "shock"


def test_single_reading_with_change_key_triggers_shock() -> None:
    # Given / When：单条期货读数带 change_pct=5.0（现值无前值，累计变动不可算）
    series = (
        Observation(signal_id=1, collected_at=_day(-1), value=1050.0, change_pct=5.0),
    )

    # Then
    verdict = evaluate_series(CU, series)
    assert verdict.triggered is True
    assert verdict.condition == "shock"
    assert verdict.stats.cum_pct is None
    assert verdict.severity == "medium"


def test_short_series_uses_available_observations_for_cumulative_window() -> None:
    # Given / When：12 点 ≥ min_observations=10，但不足 sustained_window=20
    verdict = evaluate_series(CU, _rising(100.0, 108.0, count=12))

    # Then：不足窗口时按可用点计算，而不是拒绝判定
    assert verdict.triggered is True
    assert verdict.stats.observations == 12
    assert verdict.stats.cum_window == 12
    assert verdict.stats.cum_pct == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# 四、change_key 缺失时由相邻现值推导单日变化
# ---------------------------------------------------------------------------


def test_daily_change_is_derived_from_neighbours_when_change_key_missing() -> None:
    # Given：汇率规格没有 change_key
    assert CNY.change_key is None
    series = (
        Observation(signal_id=1, collected_at=_day(-2), value=7.10, change_pct=None),
        Observation(signal_id=2, collected_at=_day(-1), value=7.32, change_pct=None),
    )

    # When / Then：+3.10% ≥ 汇率冲击线 1.5%，两个观察点即触发
    verdict = evaluate_series(CNY, series)
    assert verdict.stats.max_day_change == pytest.approx(3.0986, abs=1e-3)
    assert verdict.triggered is True
    assert verdict.condition == "shock"
    assert verdict.severity == "medium"


def test_first_observation_without_change_key_cannot_derive_daily_change() -> None:
    # Given / When：单条汇率读数没有前值，单日变化不可推导
    verdict = evaluate_series(
        CNY,
        (Observation(signal_id=1, collected_at=_day(-1), value=7.10, change_pct=None),),
    )

    # Then
    assert verdict.stats.max_day_change is None
    assert verdict.triggered is False
    assert build_filtered_reason(CNY, verdict).code == "accumulating"


def test_change_key_takes_priority_over_derived_change() -> None:
    # Given：现值环比 -9%，但 change_key 给出 +0.5%（以交易所口径为准）
    series = (
        Observation(signal_id=1, collected_at=_day(-2), value=100.0, change_pct=0.5),
        Observation(signal_id=2, collected_at=_day(-1), value=91.0, change_pct=0.5),
    )

    # Then：不得用相邻现值覆盖接口给出的 change_pct
    assert compute_stats(CU, series).max_day_change == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 五、build_result / build_filtered_reason
# ---------------------------------------------------------------------------


def test_build_result_maps_market_spec_to_industries() -> None:
    # Given / When
    verdict = evaluate_series(CU, _rising(100.0, 108.0, count=20))
    start = _day(0)

    # Then
    result = build_result(CU, verdict, start_at=start)
    assert result.event_type == "market"
    assert result.event_subtype == "commodity_price_move"
    assert result.suggested_severity == "medium"
    assert result.suggested_level is None
    assert result.confidence == DETERMINISTIC_CONFIDENCE == 0.99
    assert result.start_at == start
    assert result.end_at is None
    assert result.organizations == []
    assert result.locations == []
    assert result.affected_products == []
    assert result.affected_countries == []
    assert result.affected_currencies == []
    assert result.affected_industries == list(CU.affected_industries)
    assert "铜材" in result.affected_industries


def test_build_result_maps_fx_spec_to_currencies() -> None:
    # Given / When：汇率指标触发后只写结算币种，不写行业词表
    series = _rising(7.10, 7.32, count=20, change=None)
    verdict = evaluate_series(CNY, series)
    assert verdict.triggered is True

    # Then
    result = build_result(CNY, verdict, start_at=_day(0))
    assert result.event_type == "exchange_rate"
    assert result.event_subtype == "fx_volatility"
    assert result.affected_currencies == ["USD"]
    assert result.affected_industries == []


def test_summary_and_evidence_state_window_value_and_threshold() -> None:
    # Given / When
    result = build_result(
        CU, evaluate_series(CU, _rising(100.0, 108.0, count=20)), start_at=_day(0)
    )

    # Then：窗口、现值、基期值、阈值都必须出现在可读文本里
    text = result.summary_zh + " " + " ".join(result.evidence_sentences)
    assert "沪铜主力" in result.summary_zh
    assert "20" in result.summary_zh
    assert "108" in text
    assert "100" in text
    assert "8" in text
    assert "3" in text
    assert result.evidence_sentences
    # 无预测性表述
    for forbidden in ("预计", "可能", "将会", "有望", "预测", "建议关注"):
        assert forbidden not in text


def test_build_result_refuses_untriggered_verdict() -> None:
    # Given / When / Then
    with pytest.raises(ValueError):
        build_result(CU, evaluate_series(CU, ()), start_at=_day(0))
    with pytest.raises(ValueError):
        build_result(CU, evaluate_series(CU, _flat(100.0, count=20, change=0.1)), start_at=_day(0))


def test_build_filtered_reason_reports_empty_series() -> None:
    # Given / When
    verdict = evaluate_series(CU, ())

    # Then
    reason = build_filtered_reason(CU, verdict)
    assert reason.code == "no_observations"
    assert str(SERIES_RETENTION_DAYS) in reason.detail_zh
    assert "沪铜主力" in reason.detail_zh


def test_build_filtered_reason_refuses_triggered_verdict() -> None:
    # Given / When / Then
    verdict = evaluate_series(CU, _rising(100.0, 108.0, count=20))
    with pytest.raises(ValueError):
        build_filtered_reason(CU, verdict)


# ---------------------------------------------------------------------------
# 六、load_series：读通道、脏观测、保留期、被替代读数
# ---------------------------------------------------------------------------


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
    source.validity_policy = {"mode": "fixed_days", "fixed_days": 3650}
    session.flush()
    return source


def _commodity_extra(value: float, change: float | None = 0.5) -> dict[str, object]:
    extra: dict[str, object] = {"symbol": "nf_CU0", "last_price": value}
    if change is not None:
        extra["change_pct"] = change
    return extra


def _seed(
    session: Session,
    source_id: int,
    *,
    index: int,
    collected_at: datetime,
    extra: object = _MISSING,
    validity_state: str | None = None,
) -> RawSignal:
    payload: dict[str, object] = {"title": f"行情读数 {index}"}
    if extra is not _MISSING:
        payload["source_extra"] = extra
    extras: dict[str, object] = {}
    if validity_state is not None:
        extras = {
            "validity_state": validity_state,
            "validity_profile": "market_price_point",
            "validity_mode": "fixed_days",
            "valid_from": collected_at,
            "valid_until": collected_at + timedelta(days=30),
            "validity_policy_version": "market-eval-v1",
            "validity_reason": {
                "code": "superseded_by_newer_reading",
                "anchor_source": "collected_at",
                "details": {},
            },
        }
    signal = RawSignal(
        source_id=source_id,
        external_id=f"market-eval-{index}",
        title=f"行情读数 {index}",
        content=f"行情读数 {index}",
        collected_at=collected_at,
        fingerprint=f"market-eval-fingerprint-{index}",
        raw_data=payload,
        **extras,
    )
    session.add(signal)
    session.flush()
    return signal


def test_load_series_reads_matching_observations_in_time_order(
    db_session: Session,
) -> None:
    # Given：同信源下混有别的品种、缺现值、不可解析、缺键、过期五种脏读数
    source = _source(db_session, "commodity-futures")
    newer = _seed(
        db_session, source.id, index=1, collected_at=_day(-1),
        extra=_commodity_extra(101.0),
    )
    older = _seed(
        db_session, source.id, index=2, collected_at=_day(-2),
        extra=_commodity_extra(99.0),
    )
    _seed(
        db_session, source.id, index=3, collected_at=_day(-3),
        extra={"symbol": "nf_AL0", "last_price": 2000.0, "change_pct": 1.0},
    )
    _seed(
        db_session, source.id, index=4, collected_at=_day(-4),
        extra={"symbol": "nf_CU0", "change_pct": 1.0},
    )
    _seed(
        db_session, source.id, index=5, collected_at=_day(-5),
        extra="{不是 JSON",
    )
    _seed(
        db_session, source.id, index=6, collected_at=_day(-6),
        extra=["symbol", "nf_CU0"],
    )
    _seed(db_session, source.id, index=7, collected_at=_day(-7))
    _seed(
        db_session, source.id, index=8, collected_at=_day(-(SERIES_RETENTION_DAYS + 10)),
        extra=_commodity_extra(50.0),
    )

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)

    # Then：只保留本指标、字段完整且在保留期内的两条，按采集时间升序
    assert [item.value for item in series] == [99.0, 101.0]
    assert [item.signal_id for item in series] == [older.id, newer.id]
    assert series[0].collected_at == _day(-2)
    assert series[-1].collected_at == _day(-1)
    assert series[-1].change_pct == 0.5


def test_load_series_keeps_superseded_readings_in_window(
    db_session: Session,
) -> None:
    # Given：前一条读数已被 until_superseded 替代（validity_state=superseded）
    source = _source(db_session, "commodity-futures")
    _seed(
        db_session, source.id, index=11, collected_at=_day(-2),
        extra=_commodity_extra(100.0), validity_state="superseded",
    )
    _seed(db_session, source.id, index=12, collected_at=_day(-1), extra=_commodity_extra(108.0))

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    verdict = evaluate_series(CU, series)

    # Then：被替代的读数仍参与窗口（只剩 1 点时累计变动不可算，可据此察觉）
    assert len(series) == 2
    assert verdict.stats.cum_pct == pytest.approx(8.0)


def test_load_series_returns_empty_for_unknown_source_or_indicator(
    db_session: Session,
) -> None:
    # Given
    source = _source(db_session, "commodity-futures")

    # When / Then
    assert load_series(db_session, CU, source_id=source.id, now=NOW) == ()
    assert load_series(db_session, CU, source_id=source.id + 999_999, now=NOW) == ()


# ---------------------------------------------------------------------------
# 七、episode 归并
# ---------------------------------------------------------------------------


def _prior_analysis(
    session: Session,
    signal: RawSignal,
    *,
    result: dict[str, object] | None,
    started_at: datetime,
    provider: str = DETERMINISTIC_PROVIDER,
) -> AIAnalysisRecord:
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider=provider,
        model="deterministic-market-v1",
        prompt_version="market-eval-v1",
        status="succeeded",
        started_at=started_at,
        finished_at=started_at,
        duration_ms=1,
        result=result,
    )
    session.add(record)
    session.flush()
    return record


def _seed_tail(
    session: Session,
    source_id: int,
    *,
    offsets: tuple[int, ...],
    start: int,
    first: float = 100.0,
    step: float = 0.0,
) -> None:
    for position, offset in enumerate(offsets):
        _seed(
            session, source_id, index=start + offset + 100,
            collected_at=_day(offset), extra=_commodity_extra(first + step * position),
        )


def test_episode_continues_when_observation_gap_within_spec_gap(
    db_session: Session,
) -> None:
    # Given：上一次触发在 10 天前，此后只有 4 个观察点（≤ episode_gap=5）
    source = _source(db_session, "commodity-futures")
    prior_signal = _seed(
        db_session, source.id, index=20, collected_at=_day(-10), extra=_commodity_extra(90.0)
    )
    prior_start = _day(-12)
    _prior_analysis(
        db_session, prior_signal,
        result={"start_at": prior_start.isoformat(), "event_type": "market"},
        started_at=_day(-10),
    )
    _seed_tail(db_session, source.id, offsets=(-3, -2, -1, 0), start=0)

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    start = resolve_episode_start(
        db_session, CU, source_id=source.id, series=series, now=NOW
    )

    # Then：start_at 沿用上一次，事件身份不变
    assert start == prior_start


def test_new_episode_starts_when_observation_gap_exceeds_spec_gap(
    db_session: Session,
) -> None:
    # Given：上一次触发后有 7 个观察点（> episode_gap=5）
    source = _source(db_session, "commodity-futures")
    prior_signal = _seed(
        db_session, source.id, index=30, collected_at=_day(-20), extra=_commodity_extra(90.0)
    )
    _prior_analysis(
        db_session, prior_signal,
        result={"start_at": _day(-25).isoformat()},
        started_at=_day(-20),
    )
    _seed_tail(db_session, source.id, offsets=(-6, -5, -4, -3, -2, -1, 0), start=10)

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    start = resolve_episode_start(
        db_session, CU, source_id=source.id, series=series, now=NOW
    )

    # Then：开新 episode，start_at 取当前日期 UTC 00:00
    assert start == _day(0)


def test_episode_start_is_today_when_no_prior_trigger_exists(
    db_session: Session,
) -> None:
    # Given：只有普通 LLM 分析记录，没有 deterministic-market 记录
    source = _source(db_session, "commodity-futures")
    signal = _seed(
        db_session, source.id, index=40, collected_at=_day(-1), extra=_commodity_extra(100.0)
    )
    _prior_analysis(
        db_session, signal,
        result={"start_at": _day(-5).isoformat()},
        started_at=_day(-1), provider="openai-compatible",
    )
    _seed(db_session, source.id, index=41, collected_at=_day(0), extra=_commodity_extra(101.0))

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    start = resolve_episode_start(
        db_session, CU, source_id=source.id, series=series, now=NOW
    )

    # Then
    assert start == _day(0)


def test_episode_start_ignores_other_indicator_and_empty_results(
    db_session: Session,
) -> None:
    # Given：上一次 deterministic 记录属于别的品种，且另一条 result 为空
    source = _source(db_session, "commodity-futures")
    other = _seed(
        db_session, source.id, index=50, collected_at=_day(-10),
        extra={"symbol": "nf_AL0", "last_price": 2000.0, "change_pct": 1.0},
    )
    _prior_analysis(
        db_session, other, result={"start_at": _day(-30).isoformat()}, started_at=_day(-10)
    )
    current = _seed(
        db_session, source.id, index=51, collected_at=_day(-1), extra=_commodity_extra(100.0)
    )
    _prior_analysis(db_session, current, result=None, started_at=_day(-1))
    _seed(db_session, source.id, index=52, collected_at=_day(0), extra=_commodity_extra(101.0))

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    start = resolve_episode_start(
        db_session, CU, source_id=source.id, series=series, now=NOW
    )

    # Then
    assert start == _day(0)


def test_episode_start_falls_back_to_today_when_prior_result_has_no_start_at(
    db_session: Session,
) -> None:
    # Given：上一次记录 result 里没有可解析的 start_at
    source = _source(db_session, "commodity-futures")
    prior_signal = _seed(
        db_session, source.id, index=60, collected_at=_day(-10), extra=_commodity_extra(90.0)
    )
    _prior_analysis(db_session, prior_signal, result={"event_type": "market"}, started_at=_day(-10))
    _seed_tail(db_session, source.id, offsets=(-1, 0), start=20)

    # When
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    start = resolve_episode_start(
        db_session, CU, source_id=source.id, series=series, now=NOW
    )

    # Then
    assert start == _day(0)


# ---------------------------------------------------------------------------
# 八、对抗性：评估器只读，绝不写库
# ---------------------------------------------------------------------------


def test_evaluation_pipeline_writes_nothing_to_database(
    db_session: Session,
) -> None:
    # Given：20 个观察点构成一次恰好 8% 的触发
    source = _source(db_session, "commodity-futures")
    _seed_tail(
        db_session, source.id,
        offsets=tuple(range(-19, 1)), start=100, first=100.0, step=8.0 / 19,
    )
    signals_before = db_session.scalar(select(func.count(RawSignal.id)))
    analyses_before = db_session.scalar(select(func.count(AIAnalysisRecord.id)))

    # When：跑完整条确定性评估链
    series = load_series(db_session, CU, source_id=source.id, now=NOW)
    verdict = evaluate_series(CU, series)
    result = build_result(CU, verdict, start_at=_day(0))
    start = resolve_episode_start(
        db_session, CU, source_id=source.id, series=series, now=NOW
    )

    # Then：只读，库内行数不变，也不产生任何模型调用
    assert verdict.triggered is True
    assert result.suggested_severity == "medium"
    assert start == _day(0)
    assert db_session.scalar(select(func.count(RawSignal.id))) == signals_before
    assert db_session.scalar(select(func.count(AIAnalysisRecord.id))) == analyses_before


def test_registry_specs_are_untouched_by_evaluation() -> None:
    # Given / When / Then：评估过程只读注册表规格，不就地改写阈值
    original = (
        CU.direction,
        CU.sustained_change_pct,
        CU.sustained_window,
        CU.shock_change_pct,
        CU.min_observations,
        CU.episode_gap,
    )
    evaluate_series(_spec("fut-CU"), _rising(100.0, 130.0, count=20))
    assert (
        CU.direction,
        CU.sustained_change_pct,
        CU.sustained_window,
        CU.shock_change_pct,
        CU.min_observations,
        CU.episode_gap,
    ) == original