"""行情指标注册表与运行时配置加载（Todo 7）。

锁定三件最容易静默失效的事：
1. 19 个指标齐备，且 ``extra_match`` 的键名/取值与适配器 ``RawSourceItem.extra``
   逐字一致（不一致时 ``load_series`` 恒空、指标永远停在"数据积累中"却不报错）；
2. 默认阈值落在可运营区间，不会因为手滑写出 0 或 999 这类永不/恒触发的配置；
3. DB 运行时覆盖只在合法范围内生效，非法/越界值一律回退代码默认。
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator, Mapping

import httpx
import pytest
from sqlalchemy.orm import Session

from app.market import config_store
from app.market.config_store import (
    MARKET_CONFIG_KEY,
    MarketConfig,
    invalidate_market_config_cache,
    load_market_config,
)
from app.market.registry import INDICATORS, IndicatorSpec, resolve_indicator
from app.risks.models import RuleDimensionConfig
from app.signals.sources import CommodityFuturesAdapter, FxRatesAdapter

FUTURES_CODES = {
    "fut-CU", "fut-AL", "fut-ZN", "fut-NI", "fut-SN", "fut-PB", "fut-AU", "fut-AG",
    "fut-SC", "fut-FU", "fut-RB", "fut-SS", "fut-TA", "fut-RU",
}  # fmt: skip
FX_CODES = {"fx-CNY", "fx-JPY", "fx-CNY-JPY", "fx-CNY-KRW"}
SEMI_CODES = {"semi-980017"}

# 计划书锁定的默认分档下限（期货 ≥15/≥10/≥5、汇率 ≥8/≥5/≥3、半导体 ≥25/≥15/≥8）
PLAN_BAND_FLOORS = {
    "fut-CU": (15.0, 10.0, 5.0),
    "fx-CNY": (8.0, 5.0, 3.0),
    "semi-980017": (25.0, 15.0, 8.0),
}

# 半导体适配器（Todo 9）尚未落地，此处按计划的 extra 契约固化 fixture。
# 键名一旦与将来适配器不一致，本文件的逐条断言会先失败。
SEMICONDUCTOR_FIXTURE_EXTRA: dict[str, object] = {
    "index_code": "980017",
    "last_price": 4321.56,
    "change_pct": 1.24,
}


@pytest.fixture(autouse=True)
def _isolated_market_config_cache() -> Generator[None]:
    """配置缓存是模块级全局，用例前后强制失效，避免跨用例脏状态。"""
    invalidate_market_config_cache()
    yield
    invalidate_market_config_cache()


def _commodity_body() -> str:
    """新浪期货行情报文：14 个品种主力连续各一条（昨结 1000 → 最新 1050）。"""
    symbols = ("nf_CU0", "nf_AL0", "nf_ZN0", "nf_NI0", "nf_SN0", "nf_PB0",
               "nf_AU0", "nf_AG0", "nf_SC0", "nf_FU0", "nf_RB0", "nf_SS0",
               "nf_TA0", "nf_RU0")  # fmt: skip
    rows = [
        f'var hq_str_{symbol}="品种,150000,1000.000,1020.000,1000.000,2000.000,'
        f"1050.000,2000.000,2000.000,1000.000,900.000,1,2,100.000,50,,"
        f'2026-10-19";\n'
        for symbol in symbols
    ]
    return "".join(rows)


def _fx_body() -> str:
    return (
        '{"result":"success","time_last_update_utc":"Sun, 19 Oct 2026 00:00:01 +0000",'
        '"rates":{"CNY":7.1042,"JPY":151.44,"KRW":1432.5,"AUD":1.5}}'
    )


def _commodity_extras() -> dict[str, dict[str, object]]:
    """适配器实际产出的 extra：{symbol: extra}（不走网络）。"""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, content=_commodity_body().encode("gbk")
        )
    )
    items = asyncio.run(CommodityFuturesAdapter(transport=transport).fetch())
    assert len(items) == 14, "适配器应产出 14 个品种行情条目"
    return {str(item.extra["symbol"]): item.extra for item in items}


def _fx_extras() -> dict[str, dict[str, object]]:
    """适配器实际产出的 extra：{pair: extra}（不走网络）。

    按 ``pair`` 建索引：四条指标的报价货币会重复（USD/JPY 与 CNY/JPY 都是 JPY），
    用 ``currency`` 建索引会在人民币口径的观察点上取不到键。
    """
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=_fx_body().encode("utf-8"))
    )
    items = asyncio.run(FxRatesAdapter(transport=transport).fetch())
    assert len(items) == 4, "适配器应产出 4 条汇率条目（2 美元口径 + 2 人民币口径）"
    return {str(item.extra["pair"]): item.extra for item in items}


def _all_fixture_extras() -> dict[str, Mapping[str, object]]:
    """按 source_code 汇总全部 fixture extra，供逐条绑定断言使用。"""
    return {
        "commodity-futures": _commodity_extras(),
        "fx-rates": _fx_extras(),
        "semiconductor-index": {"980017": SEMICONDUCTOR_FIXTURE_EXTRA},
    }


def _spec(code: str) -> IndicatorSpec:
    for spec in INDICATORS:
        if spec.code == code:
            return spec
    raise AssertionError(f"注册表缺少指标 {code}")


def _write_config(db_session: Session, config: object, *, enabled: bool = True) -> None:
    db_session.add(
        RuleDimensionConfig(
            key=MARKET_CONFIG_KEY,
            label="market config",
            enabled=enabled,
            config=config,  # type: ignore[arg-type]
        )
    )
    db_session.commit()
    invalidate_market_config_cache()


# ---------------------------------------------------------------------------
# 一、20 个指标齐备与字段完整性
# ---------------------------------------------------------------------------


def test_registry_contains_expected_indicators() -> None:
    # Given / When / Then
    codes = {spec.code for spec in INDICATORS}
    assert len(INDICATORS) == 19
    assert codes == FUTURES_CODES | FX_CODES | SEMI_CODES
    assert sum(1 for s in INDICATORS if s.source_code == "commodity-futures") == 14
    assert sum(1 for s in INDICATORS if s.source_code == "fx-rates") == 4
    assert sum(1 for s in INDICATORS if s.source_code == "semiconductor-index") == 1


def test_registry_codes_and_extra_match_combinations_are_unique() -> None:
    # Given / When
    combinations = [
        (spec.source_code, tuple(sorted(spec.extra_match.items()))) for spec in INDICATORS
    ]

    # Then：同一信源下不存在两条指标抢同一条读数
    assert len(set(combinations)) == len(combinations)
    assert len({spec.code for spec in INDICATORS}) == len(INDICATORS)


def test_registry_required_fields_are_non_empty() -> None:
    # Given / When / Then
    for spec in INDICATORS:
        assert spec.code.strip(), spec.code
        assert spec.label.strip(), spec.code
        assert spec.source_code.strip(), spec.code
        assert spec.unit.strip(), spec.code
        assert spec.value_key.strip(), spec.code
        assert spec.event_type.strip(), spec.code
        assert spec.event_subtype.strip(), spec.code
        assert spec.extra_match, spec.code
        for key, value in spec.extra_match.items():
            assert key.strip() and value.strip(), spec.code
        assert spec.change_key is None or spec.change_key.strip(), spec.code
        assert spec.severity_bands, spec.code


def test_registry_binds_expected_event_taxonomy_per_source() -> None:
    # Given / When / Then
    for spec in INDICATORS:
        if spec.source_code == "commodity-futures":
            assert (spec.event_type, spec.event_subtype) == (
                "market",
                "commodity_price_move",
            )
            assert spec.value_key == "last_price"
            assert spec.change_key == "change_pct"
            assert spec.affected_currencies == ()
        elif spec.source_code == "fx-rates":
            assert (spec.event_type, spec.event_subtype) == (
                "exchange_rate",
                "fx_volatility",
            )
            assert spec.value_key == "rate"
            assert spec.change_key is None, "汇率单日变化由相邻观察点推导"
            assert spec.unit == "汇率"
            assert spec.affected_industries == ()
        else:
            assert (spec.event_type, spec.event_subtype) == (
                "market",
                "industry_market_move",
            )
            assert spec.unit == "点位"
            assert spec.affected_currencies == ()


def test_registry_binding_table_matches_plan() -> None:
    # Given / When / Then：抽样锁定计划书逐指标绑定表
    copper = _spec("fut-CU")
    assert copper.extra_match == {"symbol": "nf_CU0"}
    assert copper.unit == "元/吨"
    assert "铜材" in copper.affected_industries
    assert _spec("fut-AU").unit == "元/克"
    assert _spec("fut-AG").unit == "元/千克"
    assert _spec("fut-SC").unit == "元/桶"
    assert _spec("semi-980017").extra_match == {"index_code": "980017"}
    assert "晶圆" in _spec("semi-980017").affected_industries
    # 汇率的 affected_currencies 写死为供应商结算币种口径
    assert _spec("fx-CNY").affected_currencies == ("USD",)
    assert _spec("fx-JPY").affected_currencies == ("JPY",)
    # 人民币口径的交叉汇率仍影响日元/韩元结算
    assert _spec("fx-CNY-JPY").affected_currencies == ("JPY",)
    assert _spec("fx-CNY-KRW").affected_currencies == ("KRW",)
    # 绑定键分口径：美元口径沿用 currency（历史序列），人民币口径用 pair
    assert _spec("fx-CNY").extra_match == {"currency": "CNY"}
    assert _spec("fx-JPY").extra_match == {"currency": "JPY"}
    assert _spec("fx-CNY-JPY").extra_match == {"pair": "CNY/JPY"}
    assert _spec("fx-CNY-KRW").extra_match == {"pair": "CNY/KRW"}
    assert _spec("fx-CNY-JPY").label == "人民币兑日元汇率"
    assert _spec("fx-CNY-KRW").label == "人民币兑韩元汇率"


# ---------------------------------------------------------------------------
# 二、extra_match 与适配器实际产出的 extra 逐条对齐
# ---------------------------------------------------------------------------


def test_every_indicator_matches_the_extra_produced_by_its_adapter() -> None:
    """逐条断言：注册表的 extra_match / value_key / change_key 都真实存在于 extra。

    这是"load_series 恒空却不报错"的唯一防线：键名或取值写错一个字符，
    评估器永远读不到观察点，指标只会一直显示"数据积累中"。
    """
    # Given
    extras_by_source = _all_fixture_extras()

    for spec in INDICATORS:
        source_extras = extras_by_source.get(spec.source_code, {})
        matched = [
            extra
            for key, extra in source_extras.items()
            if all(str(extra.get(name)) == value for name, value in spec.extra_match.items())
        ]
        assert len(matched) == 1, (
            f"{spec.code} 的 extra_match={dict(spec.extra_match)} "
            f"在 {spec.source_code} 的 fixture extra 中命中 {len(matched)} 条"
        )
        extra = matched[0]
        assert spec.value_key in extra, f"{spec.code} 的 value_key 不在 extra 中"
        if spec.change_key is not None:
            assert spec.change_key in extra, f"{spec.code} 的 change_key 不在 extra 中"


def test_commodity_fixture_resolves_each_symbol_to_exactly_one_indicator() -> None:
    # Given
    extras = _commodity_extras()

    # When / Then：15 条适配器读数逐条命中 15 个期货指标，一一对应
    resolved = [
        resolve_indicator("commodity-futures", extra) for extra in extras.values()
    ]
    assert all(spec is not None for spec in resolved)
    assert {spec.code for spec in resolved if spec} == FUTURES_CODES


def test_fx_fixture_resolves_each_pair_to_exactly_one_indicator() -> None:
    # Given
    extras = _fx_extras()

    # When / Then：4 条适配器读数逐条命中 4 个汇率指标，一一对应
    resolved = [resolve_indicator("fx-rates", extra) for extra in extras.values()]
    assert all(spec is not None for spec in resolved)
    assert {spec.code for spec in resolved if spec} == FX_CODES
    assert {
        pair: (spec.code if spec else None)
        for pair, spec in zip(extras, resolved, strict=True)
    } == {
        "USD/CNY": "fx-CNY",
        "USD/JPY": "fx-JPY",
        "CNY/JPY": "fx-CNY-JPY",
        "CNY/KRW": "fx-CNY-KRW",
    }
    cny = resolve_indicator("fx-rates", extras["USD/CNY"])
    assert cny is not None and cny.code == "fx-CNY"
    assert cny.value_key == "rate" and "rate" in extras["USD/CNY"]


def test_semiconductor_fixture_resolves_to_single_indicator() -> None:
    # Given / When
    spec = resolve_indicator("semiconductor-index", SEMICONDUCTOR_FIXTURE_EXTRA)

    # Then
    assert spec is not None
    assert spec.code == "semi-980017"
    assert spec.unit == "点位"


# ---------------------------------------------------------------------------
# 三、默认阈值合法性
# ---------------------------------------------------------------------------


def test_default_thresholds_are_inside_operational_bounds() -> None:
    # Given / When / Then
    for spec in INDICATORS:
        assert 0 < spec.sustained_change_pct <= 50, spec.code
        assert 0 < spec.shock_change_pct <= 50, spec.code
        assert 2 <= spec.sustained_window <= 60, spec.code
        assert 2 <= spec.shock_window <= 60, spec.code
        assert spec.min_observations >= 2, spec.code
        assert spec.episode_gap >= 0, spec.code
        assert spec.direction in {"up", "down", "both"}, spec.code


def test_severity_bands_are_strictly_descending_and_bounded() -> None:
    # Given / When / Then
    for spec in INDICATORS:
        thresholds = [band.threshold_pct for band in spec.severity_bands]
        assert all(0 < value <= 50 for value in thresholds), spec.code
        assert thresholds == sorted(thresholds, reverse=True), spec.code
        assert len(set(thresholds)) == len(thresholds), spec.code
        for band in spec.severity_bands:
            assert band.severity in {"critical", "high", "medium", "low"}, spec.code


def test_default_thresholds_match_plan_values() -> None:
    # Given / When / Then
    futures = _spec("fut-CU")
    assert (futures.sustained_change_pct, futures.sustained_window) == (8.0, 20)
    assert (futures.shock_change_pct, futures.shock_window) == (3.0, 5)
    fx = _spec("fx-CNY")
    assert (fx.sustained_change_pct, fx.sustained_window) == (3.0, 20)
    assert (fx.shock_change_pct, fx.shock_window) == (1.5, 5)
    semi = _spec("semi-980017")
    assert (semi.sustained_change_pct, semi.sustained_window) == (12.0, 20)
    assert (semi.shock_change_pct, semi.shock_window) == (4.0, 5)
    for code, floors in PLAN_BAND_FLOORS.items():
        thresholds = [band.threshold_pct for band in _spec(code).severity_bands]
        assert tuple(thresholds) == floors, code
    assert all(spec.min_observations == 10 for spec in INDICATORS)
    assert all(spec.episode_gap == 5 for spec in INDICATORS)
    assert all(spec.direction == "both" for spec in INDICATORS)


# ---------------------------------------------------------------------------
# 四、resolve_indicator 命中与未命中
# ---------------------------------------------------------------------------


def test_resolve_indicator_returns_spec_for_registered_extra() -> None:
    # Given / When
    spec = resolve_indicator("commodity-futures", {"symbol": "nf_CU0"})

    # Then
    assert spec is not None and spec.code == "fut-CU"


@pytest.mark.parametrize(
    ("source_code", "extra"),
    [
        ("commodity-futures", {"symbol": "nf_XX0"}),  # 未登记品种
        ("commodity-futures", {"symbol": "nf_CU"}),  # 取值大小写/尾部不符
        ("commodity-futures", {"last_price": 1050.0}),  # 缺 extra_match 键
        ("commodity-futures", {}),  # 空 extra
        ("commodity-futures", None),  # extra 缺失
        ("commodity-futures", ["symbol", "nf_CU0"]),  # 类型不可解析
        ("commodity-futures", "{不是 JSON"),  # 字符串不可解析
        ("fx-rates", {"currency": "AUD"}),  # 未登记币种
        ("fx-rates", {"currency": "KRW"}),  # KRW 只按 pair 绑定，单 currency 键不命中
        ("fx-rates", {"pair": "CNY/EUR"}),  # 未登记汇率对
        ("semiconductor-index", {"index_code": "980018"}),
        ("unknown-source", {"symbol": "nf_CU0"}),  # 未登记信源
        ("", {}),
    ],
)
def test_resolve_indicator_returns_none_when_not_registered(
    source_code: str, extra: object
) -> None:
    # Given / When / Then
    assert resolve_indicator(source_code, extra) is None  # type: ignore[arg-type]


def test_resolve_indicator_reads_disabled_state_from_runtime_config(
    db_session: Session,
) -> None:
    # Given：运行时配置停用 fut-CU
    _write_config(db_session, {"indicators": {"fut-CU": {"enabled": False}}})
    config = load_market_config(db_session)

    # When / Then：停用视同未登记，停用项不可解析，其余指标不受影响
    assert resolve_indicator("commodity-futures", {"symbol": "nf_CU0"}, config) is None
    assert resolve_indicator("commodity-futures", {"symbol": "nf_AL0"}, config) is not None
    assert config.resolve("commodity-futures", {"symbol": "nf_CU0"}) is None


def test_resolve_indicator_applies_threshold_overrides_from_runtime_config(
    db_session: Session,
) -> None:
    # Given：运行时收紧沪铜持续阈值
    _write_config(
        db_session,
        {"indicators": {"fut-CU": {"sustained_change_pct": 4.5, "sustained_window": 10}}},
    )

    # When
    spec = resolve_indicator(
        "commodity-futures", {"symbol": "nf_CU0"}, load_market_config(db_session)
    )

    # Then：覆盖后的规格被真正返回（未覆盖字段仍为默认）
    assert spec is not None
    assert (spec.sustained_change_pct, spec.sustained_window) == (4.5, 10)
    assert spec.shock_change_pct == 3.0
    assert spec.unit == "元/吨"


# ---------------------------------------------------------------------------
# 五、DB 运行时覆盖：生效 / 回退 / 缓存
# ---------------------------------------------------------------------------


def test_market_config_without_row_returns_code_defaults(db_session: Session) -> None:
    # Given / When
    config = load_market_config(db_session)

    # Then
    assert MARKET_CONFIG_KEY == "market-config"
    assert set(config.specs) == {spec.code for spec in INDICATORS}
    assert config.disabled == frozenset()
    assert config.spec_for("fut-CU") == _spec("fut-CU")
    assert all(config.is_enabled(spec.code) for spec in INDICATORS)


def test_market_config_valid_overrides_are_applied(db_session: Session) -> None:
    # Given
    _write_config(
        db_session,
        {
            "indicators": {
                "fut-CU": {
                    "label": "伦铜主力",
                    "direction": "up",
                    "sustained_change_pct": 6,
                    "sustained_window": 15,
                    "shock_change_pct": 2.5,
                    "shock_window": 4,
                    "min_observations": 8,
                    "episode_gap": 3,
                    "severity_bands": [
                        {"threshold_pct": 12, "severity": "critical"},
                        {"threshold_pct": 8, "severity": "high"},
                        {"threshold_pct": 4, "severity": "medium"},
                    ],
                },
                "fx-CNY-JPY": {"enabled": False},
            }
        },
    )

    # When
    config = load_market_config(db_session)
    spec = config.spec_for("fut-CU")

    # Then
    assert spec is not None
    assert spec.label == "伦铜主力"
    assert spec.direction == "up"
    assert spec.sustained_change_pct == 6.0
    assert spec.sustained_window == 15
    assert spec.shock_change_pct == 2.5
    assert spec.shock_window == 4
    assert spec.min_observations == 8
    assert spec.episode_gap == 3
    assert [(b.threshold_pct, b.severity) for b in spec.severity_bands] == [
        (12.0, "critical"),
        (8.0, "high"),
        (4.0, "medium"),
    ]
    # 未覆盖指标保持默认；停用开关生效
    assert config.spec_for("fut-AL") == _spec("fut-AL")
    assert config.is_enabled("fx-CNY-JPY") is False
    assert config.spec_for("fx-CNY-JPY") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sustained_change_pct", -1),
        ("sustained_change_pct", 0),
        ("sustained_change_pct", 51),
        ("sustained_change_pct", "8"),  # 数字字符串不参与合并，避免歧义
        ("sustained_change_pct", True),  # bool 是 int 子类，必须显式拒绝
        ("shock_change_pct", -0.1),
        ("shock_change_pct", 999),
        ("sustained_window", 0),
        ("sustained_window", 1),
        ("sustained_window", 61),
        ("shock_window", 0),
        ("shock_window", "20"),
        ("min_observations", 1),
        ("min_observations", 0),
        ("episode_gap", -1),
        ("direction", "sideways"),
        ("severity_bands", []),
        ("severity_bands", [{"threshold_pct": "12", "severity": "critical"}]),
        ("severity_bands", [{"threshold_pct": 12, "severity": "致命"}]),
        ("label", "  "),
        ("enabled", "false"),
    ],
)
def test_market_config_invalid_values_fall_back_to_defaults(
    db_session: Session, field: str, value: object
) -> None:
    # Given：单字段非法值
    _write_config(db_session, {"indicators": {"fut-CU": {field: value}}})

    # When
    spec = load_market_config(db_session).spec_for("fut-CU")

    # Then：逐字段回退默认，其余字段不受影响
    assert spec is not None
    default = _spec("fut-CU")
    if field == "enabled":
        assert spec == default
        assert load_market_config(db_session).is_enabled("fut-CU") is True
    else:
        assert getattr(spec, field) == getattr(default, field)
    assert spec.code == "fut-CU"


def test_market_config_rejects_unknown_indicators_and_illegal_shapes(
    db_session: Session,
) -> None:
    # Given：注册表外的指标 + 非法结构，注册表不得被注入
    _write_config(
        db_session,
        {
            "indicators": {
                "fut-XX": {"sustained_change_pct": 1, "enabled": False},
                "totally-made-up": {"label": "不存在"},
                "fut-AL": "不是对象",
            }
        },
    )

    # When
    config = load_market_config(db_session)

    # Then
    assert set(config.specs) == {spec.code for spec in INDICATORS}
    assert "fut-XX" not in config.specs
    assert "totally-made-up" not in config.specs
    assert config.spec_for("fut-AL") == _spec("fut-AL")


@pytest.mark.parametrize("config", [{}, [], "not-an-object", None, 7])
def test_market_config_illegal_top_level_shape_falls_back(
    db_session: Session, config: object
) -> None:
    # Given / When
    _write_config(db_session, config)

    # Then
    loaded = load_market_config(db_session)
    assert loaded.spec_for("fut-CU") == _spec("fut-CU")
    assert loaded.disabled == frozenset()


def test_market_config_disabled_row_ignores_payload(db_session: Session) -> None:
    # Given：配置行整体停用
    _write_config(
        db_session,
        {"indicators": {"fut-CU": {"sustained_change_pct": 1, "enabled": False}}},
        enabled=False,
    )

    # When / Then
    config = load_market_config(db_session)
    assert config.spec_for("fut-CU") == _spec("fut-CU")
    assert config.is_enabled("fut-CU") is True


def test_market_config_survives_database_failure() -> None:
    """对抗性：读配置抛异常时必须回退默认，而不是把行情评估链带崩。"""

    # Given
    class _BrokenSession:
        def scalar(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("db down")

    # When
    config = load_market_config(_BrokenSession())  # type: ignore[arg-type]

    # Then
    assert config.spec_for("fut-CU") == _spec("fut-CU")
    assert config.disabled == frozenset()


def test_market_config_cache_holds_for_ttl_then_reloads(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given：可控时钟
    clock = {"now": 1_000.0}
    monkeypatch.setattr(config_store, "_now", lambda: clock["now"])
    _write_config(db_session, {"indicators": {"fut-CU": {"sustained_change_pct": 6}}})
    first = load_market_config(db_session)
    assert first.spec_for("fut-CU") is not None
    assert first.spec_for("fut-CU").sustained_change_pct == 6.0

    # When：改库但不主动失效
    db_session.execute(
        RuleDimensionConfig.__table__.update()
        .where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
        .values(config={"indicators": {"fut-CU": {"sustained_change_pct": 9}}})
    )
    db_session.commit()

    # Then：TTL 内仍读缓存
    assert load_market_config(db_session).spec_for("fut-CU").sustained_change_pct == 6.0
    clock["now"] += 59.0
    assert load_market_config(db_session).spec_for("fut-CU").sustained_change_pct == 6.0
    # TTL 过期后自动重载
    clock["now"] += 2.0
    assert load_market_config(db_session).spec_for("fut-CU").sustained_change_pct == 9.0
    assert config_store.CONFIG_TTL_SECONDS == 60.0


def test_invalidate_market_config_cache_forces_reload(
    db_session: Session,
) -> None:
    # Given
    _write_config(db_session, {"indicators": {"fut-CU": {"sustained_change_pct": 6}}})
    assert load_market_config(db_session).spec_for("fut-CU").sustained_change_pct == 6.0

    # When
    db_session.execute(
        RuleDimensionConfig.__table__.update()
        .where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
        .values(config={"indicators": {"fut-CU": {"sustained_change_pct": 9}}})
    )
    db_session.commit()
    assert load_market_config(db_session).spec_for("fut-CU").sustained_change_pct == 6.0
    invalidate_market_config_cache()

    # Then
    assert load_market_config(db_session).spec_for("fut-CU").sustained_change_pct == 9.0


def test_market_config_is_immutable_snapshot(db_session: Session) -> None:
    # Given
    _write_config(db_session, {"indicators": {"fut-CU": {"sustained_change_pct": 6}}})
    config = load_market_config(db_session)

    # When / Then：调用方拿到的是快照，外部改不动注册表
    assert isinstance(config, MarketConfig)
    with pytest.raises(TypeError):
        config.specs["fut-AL"] = _spec("fut-AL")  # type: ignore[index]