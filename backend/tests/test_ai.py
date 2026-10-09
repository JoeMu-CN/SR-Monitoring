import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tyc_analysis_context import (
    build_analysis_context,
    is_supplier_profile_context,
    render_analysis_context,
)
from app.agent.tyc_report import build_risk_report
from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.providers import (
    AIProviderError,
    FakeAIProvider,
    OpenAICompatibleProvider,
    supplier_profile_system_prompt,
    system_prompt,
)
from app.ai.schemas import SignalAnalysisInput, SignalAnalysisResult
from app.config import AISettings
from app.research.reporting import ResearchEvidenceInput, ResearchReportGenerationInput
from app.signals.models import DataSource, RawSignal


def analysis_input() -> SignalAnalysisInput:
    return SignalAnalysisInput(
        signal_id=1,
        title="港口临时管制",
        content="受大风影响，部分港区临时停止装卸作业。",
        published_at="2026-08-05T09:00:00+08:00",
    )


def valid_result() -> dict[str, object]:
    return {
        "event_type": "logistics",
        "event_subtype": "transport_disruption",
        "suggested_severity": "high",
        "organizations": [],
        "locations": [{"name": "上海港", "country_code": "CN"}],
        "affected_activities": ["logistics"],
        "affected_products": [],
        "affected_industries": [],
        "start_at": "2026-08-05T09:00:00+08:00",
        "end_at": None,
        "summary_zh": "上海港部分作业受大风影响。",
        "evidence_sentences": ["受大风影响，部分港区临时停止装卸作业。"],
        "confidence": 0.9,
    }


def test_event_subtype_must_belong_to_event_type() -> None:
    payload = valid_result()
    payload["event_subtype"] = "sanctions"

    try:
        SignalAnalysisResult.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("logistics 不得使用 sanctions 细类")


def test_suggested_level_and_rationale_are_parsed() -> None:
    """新 JSON 含建议等级与理由：解析后原样保留。"""
    payload = valid_result()
    payload["suggested_level"] = "P2"
    payload["level_rationale"] = "文本明确提及港口作业中断，建议 P2。"

    result = SignalAnalysisResult.model_validate(payload)

    assert result.suggested_level == "P2"
    assert result.level_rationale == "文本明确提及港口作业中断，建议 P2。"


def test_v2_result_without_suggested_level_still_validates() -> None:
    """旧 v2 result JSON 缺新字段：默认 None，仍可 model_validate（向后兼容）。"""
    result = SignalAnalysisResult.model_validate(valid_result())

    assert result.suggested_level is None
    assert result.level_rationale is None


def test_unknown_suggested_level_is_rejected() -> None:
    payload = valid_result()
    payload["suggested_level"] = "P5"

    try:
        SignalAnalysisResult.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("非法建议等级不得通过校验")


def test_system_prompt_embeds_level_enum_in_json_schema() -> None:
    """提示词内嵌的 JSON Schema 是模型消费的等级契约：枚举限定 P1-P4。"""
    prompt = system_prompt()
    schema = json.loads(prompt[prompt.index("{") :])

    level_schema = schema["properties"]["suggested_level"]
    assert set(level_schema["anyOf"][0]["enum"]) == {"P1", "P2", "P3", "P4"}


def test_profile_system_prompt_keeps_same_machine_consumed_schema() -> None:
    """画像上下文提示词只追加叙述约束：等级枚举与 Schema 契约保持一致。"""
    profile_prompt = supplier_profile_system_prompt()
    profile_schema = json.loads(profile_prompt[profile_prompt.index("{") :])
    base_schema = json.loads(system_prompt()[system_prompt().index("{") :])

    assert profile_schema == base_schema


def import_signal(client: TestClient) -> None:
    content = json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "external_id": "AI-TEST-001",
                    "title": "港口临时管制",
                    "content": "受大风影响，部分港区临时停止装卸作业。",
                    "published_at": "2026-08-05T09:00:00+08:00",
                }
            ],
        },
        ensure_ascii=False,
    ).encode()
    response = client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", content, "application/json")},
    )
    assert response.status_code == 200


def test_fake_provider_returns_valid_structure() -> None:
    result = asyncio.run(FakeAIProvider().analyze_signal(analysis_input()))

    assert result.event_type == "other"
    assert result.summary_zh == "港口临时管制"
    assert result.confidence == 0.5
    assert result.suggested_level == "P4"
    assert result.level_rationale


def test_openai_compatible_provider_generates_research_report_with_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 321
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "研究报告",
                                    "disclaimer": "AI 生成，仅供参考。",
                                    "facts": [
                                        {
                                            "claim_id": "fact-1",
                                            "claim_type": "fact",
                                            "text": "公开来源确认了相关事实。",
                                            "citation_ids": ["citation-1"],
                                            "confidence": 80,
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
        )

    provider = OpenAICompatibleProvider(
        AISettings(
            provider="openai-compatible",
            base_url="https://model.example.test/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=5,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        provider.generate_research_report(
            ResearchReportGenerationInput(
                topic="供应链风险",
                evidence=[
                    ResearchEvidenceInput(
                        citation_id="citation-1",
                        url="https://official.example/notice",
                        quote="公开来源确认了相关事实。",
                        excerpt="公开来源确认了相关事实。",
                    )
                ],
            ),
            max_output_tokens=321,
        )
    )

    assert result.draft.title == "研究报告"
    assert result.input_tokens == 123
    assert result.output_tokens == 45


def test_openai_compatible_provider_retries_and_validates() -> None:
    attempts = 0
    dummy_credential = "test-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.headers["Authorization"] == f"Bearer {dummy_credential}"
        if attempts == 1:
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(valid_result())}}]
            },
        )

    settings = AISettings(
        provider="openai-compatible",
        base_url="https://model.example.test/v1",
        model="test-model",
        api_key=dummy_credential,
        timeout_seconds=5,
        max_retries=1,
    )
    provider = OpenAICompatibleProvider(
        settings,
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )

    result = asyncio.run(provider.analyze_signal(analysis_input()))

    assert attempts == 2
    assert result.event_type == "logistics"
    assert result.locations[0].country_code == "CN"


def test_openai_compatible_provider_does_not_retry_semantic_validation_error() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        invalid = valid_result()
        invalid["event_type"] = "not-an-event-type"
        invalid["event_subtype"] = None
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(invalid)}}]},
        )

    settings = AISettings(
        provider="openai-compatible",
        base_url="https://model.example.test/v1",
        model="test-model",
        api_key="test-secret",
        timeout_seconds=5,
        max_retries=2,
    )
    provider = OpenAICompatibleProvider(
        settings,
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )

    with pytest.raises(AIProviderError, match="结构化结果无效"):
        asyncio.run(provider.analyze_signal(analysis_input()))
    assert attempts == 1


def test_openai_compatible_provider_drops_incompatible_event_subtype() -> None:
    invalid = valid_result()
    invalid["event_type"] = "corporate"
    invalid["event_subtype"] = "transport_disruption"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(invalid)}}]},
        )

    settings = AISettings(
        provider="openai-compatible",
        base_url="https://model.example.test/v1",
        model="test-model",
        api_key="test-secret",
        timeout_seconds=5,
        max_retries=0,
    )
    provider = OpenAICompatibleProvider(
        settings,
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )

    result = asyncio.run(provider.analyze_signal(analysis_input()))

    assert result.event_type == "corporate"
    assert result.event_subtype is None


def test_openai_compatible_provider_parses_suggested_level() -> None:
    payload = valid_result()
    payload["suggested_level"] = "P3"
    payload["level_rationale"] = "证据仅支持中等风险。"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    provider = OpenAICompatibleProvider(
        AISettings(
            provider="openai-compatible",
            base_url="https://model.example.test/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=5,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )

    result = asyncio.run(provider.analyze_signal(analysis_input()))

    assert result.suggested_level == "P3"
    assert result.level_rationale == "证据仅支持中等风险。"


def test_openai_compatible_provider_rejects_invalid_suggested_level_without_retry() -> None:
    attempts = 0
    invalid = valid_result()
    invalid["suggested_level"] = "critical"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(invalid)}}]},
        )

    provider = OpenAICompatibleProvider(
        AISettings(
            provider="openai-compatible",
            base_url="https://model.example.test/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=5,
            max_retries=2,
        ),
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )

    with pytest.raises(AIProviderError, match="结构化结果无效"):
        asyncio.run(provider.analyze_signal(analysis_input()))
    assert attempts == 1


def test_signal_analysis_api_uses_fake_without_network(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    import_signal(client)
    signal = db_session.scalar(select(RawSignal))
    assert signal is not None

    response = client.post(f"/api/v1/signals/{signal.id}/analyze")

    assert response.status_code == 200
    assert response.json()["provider"] == "fake"
    assert response.json()["status"] == "succeeded"
    assert response.json()["result"]["summary_zh"] == "港口临时管制"
    records = client.get("/api/v1/ai-analysis-records", params={"signal_id": signal.id})
    assert records.json()["total"] == 1


def test_low_confidence_analysis_is_marked_for_review(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    import_signal(client)
    signal = db_session.scalar(select(RawSignal))
    assert signal is not None
    response = client.post(f"/api/v1/signals/{signal.id}/analyze")
    assert response.status_code == 200
    payload = response.json()
    assert payload["needs_review"] is True
    assert "other" in payload["review_reason"]

    summary = client.get("/api/v1/ai-review-summary")
    assert summary.status_code == 200
    assert summary.json()["needs_review"] == 1


def test_disabled_source_is_excluded_from_review_summary_and_items(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    import_signal(client)
    signal = db_session.scalar(select(RawSignal))
    assert signal is not None
    source = db_session.get(DataSource, signal.source_id)
    assert source is not None
    source.enabled = False
    db_session.commit()

    response = client.post(f"/api/v1/signals/{signal.id}/analyze")
    assert response.status_code == 200
    assert response.json()["needs_review"] is True

    summary = client.get("/api/v1/ai-review-summary")
    assert summary.status_code == 200
    assert summary.json() == {
        "needs_review": 0,
        "filtered": 0,
        "analyzed_without_alert": 0,
    }
    items = client.get("/api/v1/ai-review-items")
    assert items.status_code == 200
    assert items.json() == []


def test_provider_failure_is_recorded(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingProvider:
        provider_name = "failing-test"
        model = "test-model"

        async def analyze_signal(self, value: SignalAnalysisInput) -> object:
            raise AIProviderError("模拟模型超时")

    import_signal(client)
    signal = db_session.scalar(select(RawSignal))
    assert signal is not None
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: FailingProvider())

    response = client.post(f"/api/v1/signals/{signal.id}/analyze")

    assert response.status_code == 502
    record = db_session.scalar(select(AIAnalysisRecord))
    assert record is not None
    assert record.status == "failed"
    assert record.error == "模拟模型超时"


def test_invalid_suggested_level_is_marked_needs_review(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """模型返回非法等级串 → provider 校验失败 → 既有失败路径标 needs_review。"""
    invalid = valid_result()
    invalid["suggested_level"] = "P5"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(invalid)}}]},
        )

    provider = OpenAICompatibleProvider(
        AISettings(
            provider="openai-compatible",
            base_url="https://model.example.test/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=5,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )

    import_signal(client)
    signal = db_session.scalar(select(RawSignal))
    assert signal is not None
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)

    response = client.post(f"/api/v1/signals/{signal.id}/analyze")

    assert response.status_code == 502
    record = db_session.scalar(select(AIAnalysisRecord))
    assert record is not None
    assert record.status == "failed"
    assert record.needs_review is True
    assert record.review_reason == "AI 分类最终失败，需人工复核"


def _capturing_provider(captured: list[dict[str, object]]) -> OpenAICompatibleProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(valid_result())}}]},
        )

    return OpenAICompatibleProvider(
        AISettings(
            provider="openai-compatible",
            base_url="https://model.example.test/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=5,
            max_retries=0,
        ),
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )


def _profile_signal(db_session: Session, raw_data: dict[str, object]) -> RawSignal:
    source = db_session.scalar(select(DataSource).order_by(DataSource.id))
    assert source is not None
    signal = RawSignal(
        source_id=source.id,
        external_id="ctx-profile-signal",
        title="天眼查多维度核查：上下文测试有限公司",
        content="重点命中：风险总览：开庭公告 1 条",
        collected_at=datetime(2026, 9, 29, 4, 30, tzinfo=UTC),
        fingerprint="ctx-profile-fingerprint",
        raw_data=raw_data,
        validity_state="legacy",
        validity_reason={
            "code": "legacy_unmigrated",
            "anchor_source": "legacy",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.commit()
    return signal


def _profile_raw_data(context_payload: dict[str, object] | None) -> dict[str, object]:
    report = build_risk_report(
        {
            "status": "success",
            "company_name": "上下文测试有限公司",
            "credit_code": "91330000CTXTEST001",
            "reg_status": "存续",
            "dimensions": {
                "get_risk_overview": {
                    "status": "success",
                    "raw": "# 风险总览\n\n> 摘要：开庭公告 1 条。\n",
                    "message": None,
                }
            },
        },
        supplier_code="SUP-CTX-001",
        generated_at=datetime(2026, 9, 29, 4, 30, tzinfo=UTC),
    )
    raw_data = report.model_dump(mode="json")
    if context_payload is not None:
        raw_data["analysis_context"] = context_payload
    return raw_data


def test_analysis_content_uses_profile_context_for_tyc_signal(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """画像信号：LLM 正文是私有上下文 JSON，且只发生一次模型调用。"""
    results = {
        "status": "success",
        "company_name": "上下文测试有限公司",
        "credit_code": "91330000CTXTEST001",
        "reg_status": "存续",
        "dimensions": {
            "get_risk_overview": {
                "status": "success",
                "raw": "# 风险总览\n\n> 摘要：开庭公告 1 条。\n",
                "message": None,
            }
        },
    }
    report = build_risk_report(
        results,
        supplier_code="SUP-CTX-001",
        generated_at=datetime(2026, 9, 29, 4, 30, tzinfo=UTC),
    )
    context = build_analysis_context(results, report=report)
    signal = _profile_signal(
        db_session, _profile_raw_data(context.model_dump(mode="json"))
    )
    captured: list[dict[str, object]] = []
    provider = _capturing_provider(captured)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)

    asyncio.run(ai_service.analyze_raw_signal(db_session, signal))

    assert len(captured) == 1
    messages = captured[0]["messages"]
    sent_content = json.loads(messages[1]["content"])["content"]
    assert sent_content == render_analysis_context(context)
    assert is_supplier_profile_context(sent_content) is True


def test_analysis_content_keeps_signal_text_for_ordinary_signal(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """普通信号：正文与提示词路由都保持既有行为（无上下文、不触发画像约束）。"""
    import_signal(client)
    signal = db_session.scalar(select(RawSignal))
    assert signal is not None
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        ai_service, "get_ai_provider", lambda _settings: _capturing_provider(captured)
    )

    asyncio.run(ai_service.analyze_raw_signal(db_session, signal))

    assert len(captured) == 1
    messages = captured[0]["messages"]
    user_payload = json.loads(messages[1]["content"])
    assert user_payload["content"] == signal.content
    assert messages[0]["content"] == system_prompt()


@pytest.mark.parametrize(
    "raw_data",
    [
        {"analysis_context": {"context_version": "tyc-analysis-context-v1"}},
        {"analysis_context": "damaged"},
        {"analysis_context": None},
    ],
    ids=["missing-fields", "not-object", "none"],
)
def test_analysis_content_falls_back_to_signal_text_on_damaged_context(
    db_session: Session, monkeypatch: MonkeyPatch, raw_data: dict[str, object]
) -> None:
    """上下文缺失或损坏：回落既有正文，普通提示词，不抛错。"""
    signal = _profile_signal(db_session, {**_profile_raw_data(None), **raw_data})
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        ai_service, "get_ai_provider", lambda _settings: _capturing_provider(captured)
    )

    asyncio.run(ai_service.analyze_raw_signal(db_session, signal))

    assert len(captured) == 1
    messages = captured[0]["messages"]
    assert json.loads(messages[1]["content"])["content"] == signal.content
    assert messages[0]["content"] == system_prompt()


def test_profile_signal_uses_profile_prompt_rules(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """画像上下文的提示词走画像分支，且与通用分支的 Schema 契约一致。"""
    results = {
        "status": "success",
        "company_name": "上下文测试有限公司",
        "dimensions": {
            "get_risk_overview": {
                "status": "success",
                "raw": "# 风险总览\n\n> 摘要：开庭公告 1 条。\n",
                "message": None,
            }
        },
    }
    report = build_risk_report(
        results,
        supplier_code="SUP-CTX-001",
        generated_at=datetime(2026, 9, 29, 4, 30, tzinfo=UTC),
    )
    context = build_analysis_context(results, report=report)
    signal = _profile_signal(
        db_session, _profile_raw_data(context.model_dump(mode="json"))
    )
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        ai_service, "get_ai_provider", lambda _settings: _capturing_provider(captured)
    )

    asyncio.run(ai_service.analyze_raw_signal(db_session, signal))

    system_message = captured[0]["messages"][0]["content"]
    assert system_message == supplier_profile_system_prompt()


def test_signal_analysis_result_accepts_naive_datetime() -> None:
    """模型返回无时区时间（deepseek-v4-flash 变体）→ 自动补 UTC 不报错。"""
    result = SignalAnalysisResult.model_validate(
        {
            "event_type": "weather",
            "event_subtype": "weather_alert",
            "suggested_severity": "medium",
            "organizations": [],
            "locations": [{"name": "吉林", "country_code": "CN"}],
            "affected_activities": ["operations"],
            "affected_products": [],
            "affected_industries": [],
            "start_at": "2026-07-15T10:08:00",
            "end_at": None,
            "summary_zh": "测试摘要",
            "evidence_sentences": ["证据一"],
            "confidence": 0.9,
        }
    )
    assert result.start_at is not None
    assert result.start_at.tzinfo is not None
    assert result.start_at.utcoffset().total_seconds() == 0  # 按 UTC 补时区


def test_signal_analysis_result_naive_end_at_follows_start() -> None:
    """start_at 带时区、end_at 无时区 → 补 UTC 后保持时序校验。"""
    result = SignalAnalysisResult.model_validate(
        {
            "event_type": "other",
            "event_subtype": "other",
            "suggested_severity": "low",
            "organizations": [],
            "locations": [],
            "affected_activities": [],
            "affected_products": [],
            "affected_industries": [],
            "start_at": "2026-08-05T09:00:00+08:00",
            "end_at": "2026-08-05T10:00:00",
            "summary_zh": "测试摘要",
            "evidence_sentences": ["证据一"],
            "confidence": 0.5,
        }
    )
    assert result.end_at is not None
    assert result.end_at.tzinfo is not None
    assert result.end_at > result.start_at
