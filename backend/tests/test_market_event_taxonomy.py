"""行情类事件分类（market / exchange_rate）与 affected_currencies 契约。"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.providers import PROMPT_VERSION, _parse_analysis_result, system_prompt
from app.ai.schemas import MARKET_EVENT_TYPES, SignalAnalysisResult
from app.risks.signal_validity import classify_pending_signal
from app.risks.workbench_router import _EVENT_SUBTYPE_LABELS, _EVENT_TYPE_LABELS
from app.signals.models import DataSource, RawSignal
from app.signals.validity import (
    EVENT_SUBTYPE_PROFILES,
    LifecycleAction,
    ValidityMode,
    ValidityProfile,
    ValidityState,
)

NOW_UTC = datetime(2026, 9, 5, 12, tzinfo=UTC)


def _result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_type": "market",
        "event_subtype": "commodity_price_move",
        "suggested_severity": "medium",
        "summary_zh": "伦铜价格单日上涨 3.2%，创近三个月新高。",
        "evidence_sentences": ["伦铜价格单日上涨 3.2%，创近三个月新高。"],
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("event_type", "event_subtype"),
    [
        ("market", "commodity_price_move"),
        ("market", "industry_market_move"),
        ("exchange_rate", "fx_volatility"),
    ],
)
def test_market_taxonomy_when_legal_combination_is_used_then_accepted(
    event_type: str, event_subtype: str
) -> None:
    # Given / When
    result = SignalAnalysisResult.model_validate(
        _result_payload(event_type=event_type, event_subtype=event_subtype)
    )

    # Then
    assert result.event_type == event_type
    assert result.event_subtype == event_subtype
    assert result.affected_currencies == []


def test_market_taxonomy_when_market_uses_fx_subtype_then_rejected() -> None:
    # Given：fx_volatility 只能挂在 exchange_rate 下
    payload = _result_payload(event_type="market", event_subtype="fx_volatility")

    # When / Then
    with pytest.raises(ValidationError) as error:
        SignalAnalysisResult.model_validate(payload)

    assert "event_subtype 与 event_type 不兼容" in str(error.value)


@pytest.mark.parametrize(
    "event_type",
    ["market", "exchange_rate"],
)
def test_market_event_types_when_exported_then_contains_exactly_new_types(
    event_type: str,
) -> None:
    # Given / When / Then
    assert MARKET_EVENT_TYPES == frozenset({"market", "exchange_rate"})
    assert event_type in MARKET_EVENT_TYPES


def test_market_event_types_when_event_type_is_validated_then_accepted() -> None:
    # Given / When
    result = SignalAnalysisResult.model_validate(
        _result_payload(event_type="exchange_rate", event_subtype="fx_volatility")
    )

    # Then
    assert result.event_type in MARKET_EVENT_TYPES


@pytest.mark.parametrize("code", ["USD", "CNY", "EUR", "JPY"])
def test_affected_currencies_when_iso4217_code_is_used_then_accepted(code: str) -> None:
    # Given / When
    result = SignalAnalysisResult.model_validate(
        _result_payload(affected_currencies=[code])
    )

    # Then
    assert result.affected_currencies == [code]


@pytest.mark.parametrize("code", ["usd", "cny", "US", "USD+", "$USD", "USD ", "US1"])
def test_affected_currencies_when_code_format_is_invalid_then_rejected(code: str) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        SignalAnalysisResult.model_validate(_result_payload(affected_currencies=[code]))


def test_affected_currencies_when_historical_result_json_has_no_field_then_defaults_empty() -> None:
    # Given：v6 及更早版本落库的 result JSON 不含 affected_currencies
    historical = _result_payload(event_type="logistics", event_subtype="transport_disruption")
    assert "affected_currencies" not in historical

    # When
    result = SignalAnalysisResult.model_validate(historical)

    # Then
    assert result.affected_currencies == []


def test_affected_currencies_when_absent_from_stored_record_json_then_deserializes() -> None:
    # Given：AIAnalysisRecord.result 里没有新字段
    stored = {
        "event_type": "market",
        "event_subtype": "industry_market_move",
        "suggested_severity": "low",
        "summary_zh": "国内螺纹钢价格指数环比下行 1.1%。",
        "evidence_sentences": ["国内螺纹钢价格指数环比下行 1.1%。"],
        "confidence": 0.7,
    }

    # When / Then
    result = SignalAnalysisResult.model_validate(json.loads(json.dumps(stored)))
    assert result.affected_currencies == []


@pytest.mark.parametrize(
    ("event_subtype", "expected_profile"),
    [
        ("commodity_price_move", ValidityProfile.MARKET_PRICE_POINT),
        ("industry_market_move", ValidityProfile.INDUSTRY_CAPACITY_SHIFT),
        ("fx_volatility", ValidityProfile.MARKET_PRICE_POINT),
    ],
)
def test_event_subtype_profiles_when_market_subtype_is_used_then_resolves(
    event_subtype: str, expected_profile: ValidityProfile
) -> None:
    # Given / When / Then
    assert EVENT_SUBTYPE_PROFILES[event_subtype] is expected_profile
    assert expected_profile in set(ValidityProfile)


def _market_source(db_session: Session, code: str) -> DataSource:
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
    db_session.add(source)
    db_session.flush()
    return source


def _pending_market_signal(
    db_session: Session, source: DataSource, suffix: str
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=f"market-{suffix}",
        title=f"LME 铜价日报 {suffix}",
        content="伦铜价格单日上涨 3.2%，创近三个月新高。",
        published_at=NOW_UTC - timedelta(hours=6),
        collected_at=NOW_UTC,
        fingerprint=f"market-taxonomy-{suffix}",
        raw_data={},
        validity_key=f"market-key-{suffix}",
        validity_state=ValidityState.PENDING_CLASSIFICATION,
        validity_reason={
            "code": "pending_classification",
            "anchor_source": "published_at",
            "details": {},
        },
        lifecycle_action=LifecycleAction.ASSERT,
    )
    db_session.add(signal)
    db_session.flush()
    return signal


@pytest.mark.parametrize(
    ("event_type", "event_subtype", "expected_profile"),
    [
        ("market", "commodity_price_move", ValidityProfile.MARKET_PRICE_POINT),
        ("market", "industry_market_move", ValidityProfile.INDUSTRY_CAPACITY_SHIFT),
        ("exchange_rate", "fx_volatility", ValidityProfile.MARKET_PRICE_POINT),
    ],
)
def test_classify_pending_signal_when_market_signal_is_pending_then_no_keyerror(
    db_session: Session,
    event_type: str,
    event_subtype: str,
    expected_profile: ValidityProfile,
) -> None:
    # Given：待分类的行情信号，信源未显式声明 profile（策略来自细类映射）
    source = _market_source(db_session, f"test-market-{event_subtype}")
    signal = _pending_market_signal(db_session, source, event_subtype)
    result = SignalAnalysisResult.model_validate(
        _result_payload(event_type=event_type, event_subtype=event_subtype)
    )
    analysis = AIAnalysisRecord(
        signal_id=signal.id,
        provider="taxonomy-test",
        model="taxonomy-v1",
        prompt_version=PROMPT_VERSION,
        status="succeeded",
        finished_at=NOW_UTC,
        result=result.model_dump(mode="json"),
    )
    db_session.add(analysis)
    db_session.flush()

    # When：旧实现在此抛 KeyError（EVENT_SUBTYPE_PROFILES 缺少新细类）
    # 行情点位是 until_superseded 语义，必须带 validity_key 才能算出有效窗口
    classify_pending_signal(db_session, signal, analysis, result, now_utc=NOW_UTC)
    db_session.flush()

    # Then：直接解析为行情有效期窗口，并标记分类完成
    assert signal.validity_state == ValidityState.ACTIVE
    assert signal.validity_reason["code"] == "classification_resolved"
    assert signal.validity_reason["details"]["event_subtype"] == event_subtype
    assert signal.validity_profile == expected_profile
    assert signal.validity_mode == ValidityMode.UNTIL_SUPERSEDED
    assert signal.valid_from == signal.published_at
    assert signal.validity_policy_version is not None
    assert analysis.needs_review is not True


def test_parse_analysis_result_when_market_subtype_is_valid_then_kept() -> None:
    # Given / When
    result = _parse_analysis_result(json.dumps(_result_payload()))

    # Then
    assert result.event_type == "market"
    assert result.event_subtype == "commodity_price_move"
    assert result.affected_currencies == []


def test_parse_analysis_result_when_market_subtype_is_incompatible_then_dropped() -> None:
    # Given / When
    result = _parse_analysis_result(
        json.dumps(_result_payload(event_type="market", event_subtype="fx_volatility"))
    )

    # Then：保守降级为无细类，而不是整体解析失败
    assert result.event_type == "market"
    assert result.event_subtype is None


def test_prompt_when_market_taxonomy_is_released_then_covers_new_values() -> None:
    # Given
    prompt = system_prompt()

    # Then：提示词版本已升到 v7
    assert PROMPT_VERSION == "signal-analysis-v7"
    # 规则正文（硬编码文本）显式给出新映射
    assert "commodity_price_move" in prompt
    assert "industry_market_move" in prompt
    assert "fx_volatility" in prompt
    assert "affected_currencies" in prompt
    assert "ISO 4217" in prompt
    # JSON Schema 尾部随枚举与新字段自动扩展
    for value in ("market", "exchange_rate", "commodity_price_move", "fx_volatility"):
        assert value in prompt


def test_workbench_labels_when_market_taxonomy_is_used_then_has_chinese_labels() -> None:
    # Given / When / Then
    assert _EVENT_TYPE_LABELS["market"] == "市场行情"
    assert _EVENT_TYPE_LABELS["exchange_rate"] == "汇率波动"
    assert _EVENT_SUBTYPE_LABELS["commodity_price_move"] == "大宗商品价格异动"
    assert _EVENT_SUBTYPE_LABELS["industry_market_move"] == "行业行情异动"
    assert _EVENT_SUBTYPE_LABELS["fx_volatility"] == "汇率波动"