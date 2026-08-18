import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIProviderError, FakeAIProvider, OpenAICompatibleProvider
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
