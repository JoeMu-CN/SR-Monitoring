"""币种匹配柱（currency）单元口径测试。

覆盖：结算币种精确集合相等命中、未填币种 fail-closed、归一化（大小写/空白）、
不做国别→币种推导与部分匹配、match_type 组合排序、引擎注册。
"""

import pytest

from app.ai.schemas import SignalAnalysisResult
from app.risks.engine.config import COLUMN_CURRENCY
from app.risks.engine.matching import MATCH_ORDER, MatchCandidate, match_currencies
from app.risks.engine.processing import MATCHERS, match_type
from app.suppliers.models import Supplier

SUPPLIER_ID = 9001


def _result(**overrides: object) -> SignalAnalysisResult:
    base: dict[str, object] = {
        "event_type": "exchange_rate",
        "event_subtype": "fx_volatility",
        "suggested_severity": "medium",
        "organizations": [],
        "locations": [],
        "affected_currencies": [],
        "summary_zh": "日元兑美元单日贬值 2.1%。",
        "evidence_sentences": ["日元兑美元单日贬值 2.1%。"],
        "confidence": 0.8,
    }
    base.update(overrides)
    return SignalAnalysisResult(**base)  # type: ignore[arg-type]


def _supplier(
    *, settlement_currency: str | None, country_code: str = "JP"
) -> Supplier:
    return Supplier(
        id=SUPPLIER_ID,
        supplier_code="UNIT-CUR",
        legal_name="单元币种供应商",
        country_code=country_code,
        enabled=True,
        settlement_currency=settlement_currency,
    )


def _match(
    supplier: Supplier,
    affected_currencies: list[str],
    *,
    assoc: dict[str, int] | None = None,
) -> dict[int, MatchCandidate]:
    """币种柱单元口径：事件受影响币种 vs 供应商结算币种。"""
    matches: dict[int, MatchCandidate] = {}
    match_currencies(
        None,  # type: ignore[arg-type]  # 币种柱不访问数据库
        _result(affected_currencies=affected_currencies),
        [supplier],
        assoc or {"currency": 15},
        matches,
    )
    return matches


# ── 命中口径 ─────────────────────────────────────────────────────────


def test_currency_when_event_and_supplier_currency_equal_then_hit() -> None:
    """Given 事件受影响币种 JPY，When 供应商结算币种 JPY，Then 命中且证据完整。"""
    matches = _match(_supplier(settlement_currency="JPY"), ["JPY"])

    candidate = matches[SUPPLIER_ID]
    assert candidate.match_types == {"currency"}
    assert candidate.association_score == 15
    assert candidate.reasons == ["结算币种匹配：JPY ↔ 单元币种供应商"]
    assert candidate.evidence == [
        {
            "object_type": "currency",
            "supplier_id": SUPPLIER_ID,
            "currency": "JPY",
        }
    ]


def test_currency_when_assoc_score_configured_then_uses_configured_value() -> None:
    """Given 维度把币种分值调高，When 匹配，Then 取配置值而非硬编码默认值。"""
    matches = _match(
        _supplier(settlement_currency="JPY"), ["JPY"], assoc={"currency": 22}
    )

    assert matches[SUPPLIER_ID].association_score == 22


def test_currency_when_assoc_score_missing_then_falls_back_to_15() -> None:
    """Given 维度未配置币种分值，When 匹配，Then 回落到默认值 15。"""
    matches = _match(_supplier(settlement_currency="JPY"), ["JPY"], assoc={})

    assert matches[SUPPLIER_ID].association_score == 15


def test_currency_when_event_lists_multiple_currencies_then_only_equal_one_hits() -> None:
    """Given 事件受影响币种 [USD, JPY]，When 供应商结算 JPY，Then 只产出 JPY 一条证据。"""
    matches = _match(_supplier(settlement_currency="JPY"), ["USD", "JPY"])

    candidate = matches[SUPPLIER_ID]
    assert len(candidate.evidence) == 1
    assert candidate.evidence[0]["currency"] == "JPY"


# ── fail-closed：未填币种的供应商不命中 ───────────────────────────────


@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_currency_when_supplier_currency_is_blank_then_no_match(blank: str | None) -> None:
    """Given 供应商未填结算币种，When 事件受影响 JPY，Then 不产生任何候选（fail-closed）。"""
    assert _match(_supplier(settlement_currency=blank), ["JPY"]) == {}


def test_currency_when_event_has_no_affected_currency_then_no_match() -> None:
    """Given 事件未给出受影响币种，When 供应商结算币种 JPY，Then 不命中。"""
    assert _match(_supplier(settlement_currency="JPY"), []) == {}


# ── 不相等 / 不做部分匹配 / 不做国别推导 ─────────────────────────────


def test_currency_when_codes_differ_then_no_match() -> None:
    """Given 事件 USD、供应商 JPY，When 匹配，Then 不命中（等值匹配，非包含）。"""
    assert _match(_supplier(settlement_currency="JPY"), ["USD"]) == {}


def test_currency_when_supplier_country_implies_code_then_no_match() -> None:
    """Given 供应商国别 JP 但未填结算币种，When 事件 JPY，Then 不做国别→币种推导。"""
    assert _match(
        _supplier(settlement_currency=None, country_code="JP"), ["JPY"]
    ) == {}


def test_currency_when_codes_only_partially_overlap_then_no_match() -> None:
    """Given 供应商结算币种 JP（非完整代码），When 事件 JPY，Then 不做前缀/部分匹配。"""
    assert _match(_supplier(settlement_currency="JP"), ["JPY"]) == {}


# ── 归一化：strip().upper() 后精确相等 ────────────────────────────────


@pytest.mark.parametrize(
    "raw", [" jpy ", "jpy", "  JPY", "jPy", "\tjpy\n"]
)
def test_currency_when_case_and_space_differ_then_hit(raw: str) -> None:
    """Given 供应商结算币种存在大小写/空白差异，When 事件 JPY，Then 归一后相等命中。"""
    matches = _match(_supplier(settlement_currency=raw), ["JPY"])

    assert matches[SUPPLIER_ID].evidence[0]["currency"] == "JPY"


def test_currency_when_event_side_is_dirty_then_hit() -> None:
    """Given 事件侧历史脏数据 " jpy "（绕开 pydantic 校验），Then 与 JPY 供应商归一后命中。"""
    result = SignalAnalysisResult.model_construct(
        **{**_result().__dict__, "affected_currencies": [" jpy "]}
    )
    matches: dict[int, MatchCandidate] = {}
    match_currencies(
        None,  # type: ignore[arg-type]
        result,
        [_supplier(settlement_currency="JPY")],
        {"currency": 15},
        matches,
    )

    assert matches[SUPPLIER_ID].evidence[0]["currency"] == "JPY"


# ── 排序与注册 ───────────────────────────────────────────────────────


def test_match_order_places_currency_after_country_and_industry() -> None:
    """Given 币种柱加入 MATCH_ORDER，Then 排在 country/industry/product 之后、值唯一。"""
    assert MATCH_ORDER["currency"] == 8
    assert sorted(MATCH_ORDER.values()) == list(range(len(MATCH_ORDER)))


def test_match_type_when_currency_combined_then_orders_by_match_order() -> None:
    """Given 同一供应商被币种柱与行业柱命中，When 生成 match_type，Then 按 MATCH_ORDER 稳定排序。"""
    assert match_type({"currency", "industry"}) == "industry+currency"
    assert match_type({"currency", "country", "industry", "product"}) == (
        "country+industry+product+currency"
    )


def test_matchers_registry_includes_currency_column() -> None:
    """Given 引擎匹配柱注册表，When 查 COLUMN_CURRENCY，Then 指向 match_currencies。"""
    assert MATCHERS[COLUMN_CURRENCY] is match_currencies