import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.signals.models import RawSignal


class StaticProvider:
    provider_name = "static-test"
    model = "static-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def analyze_signal(self, _value: object) -> SignalAnalysisResult:
        self.calls += 1
        return SignalAnalysisResult(
            event_type="weather",
            suggested_severity="high",
            organizations=[{"name": "测试供应商有限公司", "aliases": []}],
            locations=[{"name": "上海市", "country_code": "CN", "city": "上海市"}],
            affected_activities=["production", "logistics"],
            affected_products=[],
            start_at="2026-08-11T08:00:00+08:00",
            end_at="2026-08-12T08:00:00+08:00",
            summary_zh="台风影响上海地区生产和物流",
            evidence_sentences=["受台风影响，上海地区部分生产和物流活动暂停。"],
            confidence=0.9,
        )


def create_supplier(client: TestClient) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "supplier_code": "D5-TEST",
            "legal_name": "测试供应商有限公司",
            "country_code": "CN",
            "registry_no": "91310000D5TEST001",
            "aliases": [],
            "sites": [],
            "products": [],
        },
    )
    assert response.status_code == 201


def import_signals(client: TestClient) -> None:
    document = {
        "version": "1.0",
        "signals": [
            {
                "external_id": "D5-SIGNAL-001",
                "title": "台风生产影响公告",
                "content": "受台风影响，测试供应商有限公司生产和物流活动暂停。",
                "url": "https://example.com/d5/001",
                "published_at": "2026-08-11T08:00:00+08:00",
            },
            {
                "external_id": "D5-SIGNAL-002",
                "title": "台风公告转载",
                "content": "测试供应商有限公司受同一台风事件影响。",
                "url": "https://example.com/d5/002",
                "published_at": "2026-08-11T09:00:00+08:00",
            },
        ],
    }
    response = client.post(
        "/api/v1/signals/import",
        files={
            "file": (
                "signals.json",
                json.dumps(document, ensure_ascii=False).encode(),
                "application/json",
            )
        },
    )
    assert response.status_code == 200


def test_complete_risk_pipeline_merges_and_scores_idempotently(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_supplier(client)
    import_signals(client)
    signal_ids = list(db_session.scalars(select(RawSignal.id).order_by(RawSignal.id)))

    first = client.post(f"/api/v1/signals/{signal_ids[0]}/process")
    second = client.post(f"/api/v1/signals/{signal_ids[1]}/process")
    repeated = client.post(f"/api/v1/signals/{signal_ids[0]}/process")

    assert first.status_code == second.status_code == repeated.status_code == 200
    assert first.json()["event_created"] is True
    assert second.json()["event_created"] is False
    assert repeated.json()["signal_linked"] is False
    assert provider.calls == 2
    assert db_session.scalar(select(func.count()).select_from(AIAnalysisRecord)) == 2
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskEventSignal)) == 2
    assert db_session.scalar(select(func.count()).select_from(SupplierEventMatch)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 1

    response = client.get("/api/v1/risk-alerts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    alert = payload["items"][0]
    assert alert["level"] == "P2"
    assert alert["score"] == 73
    assert alert["supplier_name"] == "测试供应商有限公司"
    assert alert["match_type"] == "legal_name"
    assert alert["score_detail"]["rule_version"] == "risk-score-v0"


def test_process_without_matching_supplier_creates_event_without_alert(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    assert response.json()["alert_ids"] == []
    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 1
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0
