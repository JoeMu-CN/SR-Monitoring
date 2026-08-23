"""signal-filter 配置化测试（load_filter_rules 覆盖/缓存/回退）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.risks.models import RuleDimensionConfig
from app.signals.relevance import (
    FILTER_CONFIG_KEY,
    _default_rules,
    invalidate_filter_rules_cache,
    load_filter_rules,
)


def test_default_rules_when_no_config(db_session: Session) -> None:
    """无配置行 → 返回代码默认值。"""
    rules = load_filter_rules(db_session)
    defaults = _default_rules()
    assert rules.high_impact == defaults.high_impact
    assert rules.priority_countries == defaults.priority_countries
    assert rules.list_sources == defaults.list_sources
    assert "JP" in rules.priority_countries
    assert "ofac-sdn" in rules.list_sources


def test_config_override_high_impact(db_session: Session) -> None:
    """配置行覆盖高影响关键词（含新增词）。"""
    db_session.add(
        RuleDimensionConfig(
            key=FILTER_CONFIG_KEY,
            label="signal filter",
            enabled=True,
            config={"high_impact": ["新增关键词"], "priority_countries": ["SG"]},
        )
    )
    db_session.commit()
    invalidate_filter_rules_cache()
    rules = load_filter_rules(db_session)
    assert "新增关键词" in rules.high_impact
    assert rules.priority_countries == frozenset({"SG"})  # 覆盖默认 JP/KR
    assert rules.list_sources == _default_rules().list_sources  # 未配置沿用默认


def test_empty_config_field_falls_back_to_default(db_session: Session) -> None:
    """配置中某字段为空列表 → 该字段回退默认（不导致空规则集）。"""
    db_session.add(
        RuleDimensionConfig(
            key=FILTER_CONFIG_KEY,
            label="signal filter",
            enabled=True,
            config={"high_impact": [], "priority_countries": ["VN"]},
        )
    )
    db_session.commit()
    invalidate_filter_rules_cache()
    rules = load_filter_rules(db_session)
    # 空列表回退默认高影响词；重点国取 VN
    assert rules.high_impact == _default_rules().high_impact
    assert rules.priority_countries == frozenset({"VN"})


def test_disabled_config_row_uses_defaults(db_session: Session) -> None:
    """配置行 enabled=False → 忽略配置使用默认。"""
    db_session.add(
        RuleDimensionConfig(
            key=FILTER_CONFIG_KEY,
            label="signal filter",
            enabled=False,
            config={"high_impact": ["不会生效"]},
        )
    )
    db_session.commit()
    invalidate_filter_rules_cache()
    rules = load_filter_rules(db_session)
    assert "不会生效" not in rules.high_impact
    assert rules.high_impact == _default_rules().high_impact


def test_grade_structured_signal_skips_low_volatility() -> None:
    """commodity 低涨跌幅 → 返回跳过 reason（免 LLM）。"""
    from app.signals.relevance import _default_rules, grade_structured_signal

    reason = grade_structured_signal(
        "commodity-futures",
        "棉花主力 17065 元/吨（+0.03%）",
        "最新价：17065.0 元/吨；较昨结算：+0.03%",
        _default_rules(),
    )
    assert reason is not None
    assert "低于阈值" in reason


def test_grade_structured_signal_passes_high_volatility() -> None:
    """commodity 高涨跌幅（≥5%）→ 放行进入分析链。"""
    from app.signals.relevance import _default_rules, grade_structured_signal

    reason = grade_structured_signal(
        "commodity-futures",
        "沪铜主力 118000 元/吨（+6.25%）",
        "最新价：118000.0 元/吨；较昨结算：+6.25%",
        _default_rules(),
    )
    assert reason is None


def test_grade_structured_signal_non_commodity_passes() -> None:
    """非 commodity 信源 → 不受分级影响（放行）。"""
    from app.signals.relevance import _default_rules, grade_structured_signal

    assert grade_structured_signal(
        "pbc-lpr", "LPR 报价", "内容", _default_rules()
    ) is None


def test_grade_structured_signal_unparseable_passes() -> None:
    """解析不到涨跌幅 → 保守放行。"""
    from app.signals.relevance import _default_rules, grade_structured_signal

    assert grade_structured_signal(
        "commodity-futures", "异常标题", "无涨跌幅信息", _default_rules()
    ) is None


def test_filter_rules_threshold_config(db_session: Session) -> None:
    """配置 commodity_threshold_pct 覆盖生效（高阈值 → 低波动也跳过）。"""
    from app.signals.relevance import (
        FILTER_CONFIG_KEY,
        invalidate_filter_rules_cache,
        load_filter_rules,
    )

    db_session.add(
        RuleDimensionConfig(
            key=FILTER_CONFIG_KEY,
            label="signal filter",
            enabled=True,
            config={"commodity_threshold_pct": 1.0},
        )
    )
    db_session.commit()
    invalidate_filter_rules_cache()
    rules = load_filter_rules(db_session)
    assert rules.commodity_threshold_pct == 1.0
    # 0.03% < 1% 阈值 → 跳过
    from app.signals.relevance import grade_structured_signal

    reason = grade_structured_signal(
        "commodity-futures",
        "棉花主力 17065 元/吨（+0.03%）",
        "较昨结算：+0.03%",
        rules,
    )
    assert reason is not None
