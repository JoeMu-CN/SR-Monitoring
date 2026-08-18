import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal


class StaticProvider:
    provider_name = "static-test"
    model = "static-v1"

    def __init__(self, result: SignalAnalysisResult | None = None) -> None:
        self.calls = 0
        self.result = result

    async def analyze_signal(self, _value: object) -> SignalAnalysisResult:
        self.calls += 1
        return self.result or SignalAnalysisResult(
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


def create_rich_supplier(client: TestClient, *, district: str = "浦东新区") -> None:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "supplier_code": "D6-TEST",
            "legal_name": "德六精密制造有限公司",
            "country_code": "CN",
            "registry_no": "91310000D6TEST001",
            "aliases": [{"alias": "D6 Precision", "language": "en"}],
            "sites": [
                {
                    "site_name": "上海工厂",
                    "country_code": "CN",
                    "region": "上海市",
                    "city": "上海市",
                    "district": district,
                    "address": "上海市浦东新区测试路1号",
                    "latitude": 31.2304,
                    "longitude": 121.4737,
                }
            ],
            "products": [
                {
                    "name": "精密零部件",
                    "keywords": ["轴承", "精密加工"],
                }
            ],
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
    assert alert["score_detail"]["rule_version"].startswith("risk-score-v1-")


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


def test_alias_match_persists_entity_and_structured_evidence(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    result = SignalAnalysisResult(
        event_type="corporate",
        suggested_severity="high",
        organizations=[{"name": "D6 Precision", "aliases": []}],
        locations=[],
        affected_activities=["operations"],
        affected_products=[],
        summary_zh="D6 Precision 暂停部分经营活动",
        evidence_sentences=["D6 Precision 暂停部分经营活动。"],
        confidence=0.88,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_rich_supplier(client)
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    match = db_session.scalar(select(SupplierEventMatch))
    assert match is not None
    assert match.match_type == "alias"
    assert match.evidence[0]["object_type"] == "alias"
    assert db_session.scalar(select(func.count()).select_from(EventEntity)) == 1


def test_postgis_distance_match_caps_weak_association_at_p2(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    result = SignalAnalysisResult(
        event_type="weather",
        suggested_severity="critical",
        organizations=[],
        locations=[
            {
                "name": "台风中心",
                "country_code": "CN",
                "latitude": 31.23,
                "longitude": 121.47,
                "radius_km": 50,
            }
        ],
        affected_activities=["production", "logistics"],
        affected_products=[],
        summary_zh="台风影响上海周边生产和物流",
        evidence_sentences=["台风中心周边50公里受影响。"],
        confidence=0.95,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_rich_supplier(client)
    import_signals(client)
    source = db_session.scalar(select(DataSource).where(DataSource.code == "manual-json"))
    assert source is not None
    source.credibility = 100
    db_session.flush()
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    match = db_session.scalar(select(SupplierEventMatch))
    assert alert is not None and match is not None
    assert match.match_type == "site_distance"
    assert match.evidence[0]["distance_km"] < 1
    assert alert.score == 85
    assert alert.level == "P2"
    assert alert.score_detail["level_cap"] == "weak_association_max_p2"
    assert db_session.scalar(select(func.count()).select_from(EventLocation)) == 1


def test_district_location_match_rejects_same_city_other_district(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    result = SignalAnalysisResult(
        event_type="weather",
        suggested_severity="high",
        organizations=[],
        locations=[
            {
                "name": "上海市浦东新区",
                "country_code": "CN",
                "region": "上海市",
                "city": "上海市",
                "district": "浦东新区",
            }
        ],
        affected_activities=["production"],
        affected_products=[],
        summary_zh="浦东新区天气影响",
        evidence_sentences=["浦东新区发布天气风险预警。"],
        confidence=0.9,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_rich_supplier(client, district="宝山区")
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    assert response.json()["alert_ids"] == []
    assert db_session.scalar(select(func.count()).select_from(SupplierEventMatch)) == 0
    event_location = db_session.scalar(select(EventLocation))
    assert event_location is not None
    assert event_location.district == "浦东新区"

    detail = client.get(f"/api/v1/events/{event_location.event_id}")
    assert detail.status_code == 200
    assert detail.json()["locations"][0]["district"] == "浦东新区"


def test_district_in_location_name_rejects_site_city_other_district(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """事件未填 district 时，也不能因 city/region 字段错位跨区命中。"""
    result = SignalAnalysisResult(
        event_type="weather",
        suggested_severity="high",
        organizations=[],
        locations=[
            {
                "name": "上海市宝山区",
                "country_code": "CN",
                "region": "上海市",
                "city": "上海市",
            }
        ],
        affected_activities=["production"],
        affected_products=[],
        summary_zh="宝山区天气影响",
        evidence_sentences=["宝山区发布天气风险预警。"],
        confidence=0.9,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    response = client.post(
        "/api/v1/suppliers",
        json={
            "supplier_code": "D7-TEST",
            "legal_name": "上海松江测试有限公司",
            "country_code": "CN",
            "registry_no": "91310000D7TEST001",
            "aliases": [],
            "sites": [
                {
                    "site_name": "上海松江",
                    "country_code": "CN",
                    "region": "上海市",
                    "city": "松江区",
                    "district": None,
                    "address": "上海市松江区测试路1号",
                    "latitude": None,
                    "longitude": None,
                }
            ],
            "products": [],
        },
    )
    assert response.status_code == 201
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    processed = client.post(f"/api/v1/signals/{signal_id}/process")

    assert processed.status_code == 200
    assert processed.json()["alert_ids"] == []
    assert db_session.scalar(select(func.count()).select_from(SupplierEventMatch)) == 0


def test_district_spatial_match_rejects_same_district_in_wrong_city(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    result = SignalAnalysisResult(
        event_type="weather",
        suggested_severity="high",
        organizations=[],
        locations=[
            {
                "name": "南京市浦东新区",
                "country_code": "CN",
                "region": "江苏省",
                "city": "南京市",
                "district": "浦东新区",
                "latitude": 31.2304,
                "longitude": 121.4737,
                "radius_km": 50,
            }
        ],
        affected_activities=["production"],
        affected_products=[],
        summary_zh="南京市浦东新区天气影响",
        evidence_sentences=["南京市浦东新区发布天气风险预警。"],
        confidence=0.9,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_rich_supplier(client, district="浦东新区")
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    assert response.json()["alert_ids"] == []
    assert db_session.scalar(select(func.count()).select_from(SupplierEventMatch)) == 0


def test_product_keyword_match_sets_product_relevance(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    result = SignalAnalysisResult(
        event_type="logistics",
        suggested_severity="medium",
        organizations=[],
        locations=[],
        affected_activities=["logistics"],
        affected_products=["高精度轴承"],
        summary_zh="高精度轴承运输延迟",
        evidence_sentences=["高精度轴承运输预计延迟。"],
        confidence=0.82,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_rich_supplier(client)
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    match = db_session.scalar(select(SupplierEventMatch))
    assert alert is not None and match is not None
    assert match.match_type == "product"
    assert match.evidence[0]["product_name"] == "精密零部件"
    assert alert.score == 57
    assert alert.level == "P3"
    assert alert.score_detail["product_relevance"] == 5


def test_dashboard_summary_reflects_current_alerts(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """验收场景支持：风险总览汇总 P1-P4、今日新增、类型分布与数据源状态。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_supplier(client)
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None
    response = client.post(f"/api/v1/signals/{signal_id}/process")
    assert response.status_code == 200

    summary = client.get("/api/v1/dashboard/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["total_current"] >= 1
    assert payload["level_counts"][0]["level"] == "P1"
    assert sum(item["count"] for item in payload["level_counts"]) == payload["total_current"]
    assert payload["today_new"] >= 1
    assert any(item["event_type"] == "weather" for item in payload["type_distribution"])
    assert len(payload["recent_alerts"]) >= 1
    codes = {source["code"] for source in payload["sources"]}
    assert "manual-json" in codes


def test_risk_alert_detail_contains_evidence_and_score(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """验收场景 8：风险详情能够查看评分明细、匹配理由与原始来源。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_supplier(client)
    import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None
    assert client.post(f"/api/v1/signals/{signal_id}/process").status_code == 200

    alert_id = db_session.scalar(select(RiskAlert.id))
    assert alert_id is not None
    response = client.get(f"/api/v1/risk-alerts/{alert_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["score_detail"]["rule_version"].startswith("risk-score-v1-")
    assert detail["match_reasons"]
    assert detail["match_evidence"]
    assert detail["source_url"] == "https://example.com/d5/001"
    assert detail["supplier_name"] == "测试供应商有限公司"

    missing = client.get("/api/v1/risk-alerts/999999")
    assert missing.status_code == 404


def test_event_detail_contains_signals_entities_locations(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """事件详情：全部信号证据、涉及主体与地点。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    create_supplier(client)
    import_signals(client)
    signal_ids = list(db_session.scalars(select(RawSignal.id).order_by(RawSignal.id)))
    assert client.post(f"/api/v1/signals/{signal_ids[0]}/process").status_code == 200
    assert client.post(f"/api/v1/signals/{signal_ids[1]}/process").status_code == 200

    event_id = db_session.scalar(select(RiskEvent.id))
    assert event_id is not None
    response = client.get(f"/api/v1/events/{event_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["event_type"] == "weather"
    assert len(detail["signals"]) == 2
    assert any(entity["name"] == "测试供应商有限公司" for entity in detail["entities"])
    assert any("上海" in str(location["city"]) for location in detail["locations"])

    missing = client.get("/api/v1/events/999999")
    assert missing.status_code == 404
