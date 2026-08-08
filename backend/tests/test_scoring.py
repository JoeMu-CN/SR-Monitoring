"""D7 可配置评分、强制规则、提醒去重和自动失效的测试。"""

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.risks.scoring import (
    ForcedRule,
    ScoringSettings,
    apply_forced_rules,
    apply_level_cap,
    compute_level,
    compute_score,
    load_scoring_settings,
)
from app.risks.service import _compute_expires_at, expire_alerts
from app.signals.models import RawSignal

# ── 可配置评分单元测试 ──────────────────────────────────────────


class TestLoadScoringSettings:
    def test_defaults_when_no_env(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("RISK_SCORING_CONFIG", raising=False)
        settings = load_scoring_settings()
        assert settings.rule_version == "risk-score-v1"
        assert settings.p1_min == 85
        assert settings.p2_min == 65
        assert settings.p3_min == 40
        assert settings.alert_expiry_days == 90
        assert len(settings.forced_rules) == 1
        assert settings.forced_rules[0].name == "sanctions_entity_hit"

    def test_valid_json_override(self, monkeypatch: MonkeyPatch) -> None:
        config = {"p1_min": 90, "alert_expiry_days": 60, "rule_version": "custom-v2"}
        monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps(config))
        settings = load_scoring_settings()
        assert settings.p1_min == 90
        assert settings.alert_expiry_days == 60
        assert settings.rule_version == "custom-v2"
        # 未覆盖的键保持默认
        assert settings.p2_min == 65

    def test_dict_merge_preserves_defaults(self, monkeypatch: MonkeyPatch) -> None:
        config = {"severity_scores": {"critical": 40}}
        monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps(config))
        settings = load_scoring_settings()
        assert settings.severity_scores["critical"] == 40
        assert settings.severity_scores["high"] == 28  # 默认值保留

    def test_invalid_json_returns_defaults(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("RISK_SCORING_CONFIG", "not-json{{{")
        settings = load_scoring_settings()
        assert settings.rule_version == "risk-score-v1"

    def test_empty_string_returns_defaults(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setenv("RISK_SCORING_CONFIG", "")
        settings = load_scoring_settings()
        assert settings.rule_version == "risk-score-v1"

    def test_custom_forced_rules(self, monkeypatch: MonkeyPatch) -> None:
        config = {
            "forced_rules": [
                {
                    "name": "custom_rule",
                    "description": "自定义强制规则",
                    "event_types": ["trade_policy"],
                    "match_types": ["registry_no"],
                    "forced_level": "P1",
                    "reason": "自定义原因",
                }
            ]
        }
        monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps(config))
        settings = load_scoring_settings()
        assert len(settings.forced_rules) == 1
        assert settings.forced_rules[0].name == "custom_rule"
        assert settings.forced_rules[0].event_types == ("trade_policy",)


class TestComputeScore:
    def test_default_scoring(self) -> None:
        settings = ScoringSettings()
        score, detail = compute_score(settings, "high", 25, 80, True, False)
        assert detail["rule_version"] == "risk-score-v1"
        assert detail["severity"] == 28
        assert detail["association"] == 25
        assert detail["source_credibility"] == 16  # 80 * 0.2
        assert detail["timeliness"] == 10
        assert detail["product_relevance"] == 0
        assert score == 28 + 25 + 16 + 10 + 0

    def test_score_capped_at_100(self) -> None:
        settings = ScoringSettings()
        score, _ = compute_score(settings, "critical", 30, 100, True, True)
        # 35 + 30 + 20 + 10 + 5 = 100
        assert score == 100

    def test_custom_severity_scores(self) -> None:
        custom = {"critical": 40, "high": 30, "medium": 22, "low": 12}
        settings = ScoringSettings(severity_scores=custom)
        score, detail = compute_score(settings, "critical", 0, 0, False, False)
        assert detail["severity"] == 40

    def test_unknown_severity_gets_zero(self) -> None:
        settings = ScoringSettings()
        _, detail = compute_score(settings, "unknown", 0, 0, False, False)
        assert detail["severity"] == 0


class TestComputeLevel:
    def test_default_thresholds(self) -> None:
        settings = ScoringSettings()
        assert compute_level(settings, 100) == "P1"
        assert compute_level(settings, 85) == "P1"
        assert compute_level(settings, 84) == "P2"
        assert compute_level(settings, 65) == "P2"
        assert compute_level(settings, 64) == "P3"
        assert compute_level(settings, 40) == "P3"
        assert compute_level(settings, 39) == "P4"
        assert compute_level(settings, 0) == "P4"

    def test_custom_thresholds(self) -> None:
        settings = ScoringSettings(p1_min=90, p2_min=70, p3_min=50)
        assert compute_level(settings, 89) == "P2"
        assert compute_level(settings, 90) == "P1"
        assert compute_level(settings, 50) == "P3"
        assert compute_level(settings, 49) == "P4"


class TestApplyLevelCap:
    def test_strong_match_no_cap(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level = apply_level_cap(settings, "P1", "legal_name", detail)
        assert level == "P1"
        assert "level_cap" not in detail

    def test_weak_match_capped_at_p2(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level = apply_level_cap(settings, "P1", "site_distance", detail)
        assert level == "P2"
        assert detail["level_cap"] == "weak_association_max_p2"

    def test_non_p1_not_affected(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level = apply_level_cap(settings, "P2", "site_distance", detail)
        assert level == "P2"
        assert "level_cap" not in detail

    def test_custom_strong_types(self) -> None:
        settings = ScoringSettings(strong_match_types=frozenset({"registry_no"}))
        detail: dict[str, object] = {}
        level = apply_level_cap(settings, "P1", "legal_name", detail)
        assert level == "P2"


class TestApplyForcedRules:
    def test_sanctions_entity_hit_forces_p1(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level, score = apply_forced_rules(
            settings, "compliance", "legal_name", "P3", 55, detail
        )
        assert level == "P1"
        assert score == 100
        forced = detail["forced_rule"]
        assert isinstance(forced, dict)
        assert forced["name"] == "sanctions_entity_hit"
        assert forced["original_level"] == "P3"
        assert forced["original_score"] == 55

    def test_non_matching_event_type_passes_through(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level, score = apply_forced_rules(
            settings, "weather", "legal_name", "P3", 55, detail
        )
        assert level == "P3"
        assert score == 55
        assert "forced_rule" not in detail

    def test_non_matching_match_type_passes_through(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level, score = apply_forced_rules(
            settings, "compliance", "site_distance", "P3", 55, detail
        )
        assert level == "P3"
        assert score == 55
        assert "forced_rule" not in detail

    def test_non_matching_event_subtype_passes_through(self) -> None:
        rule = ForcedRule(
            name="sanctions_only",
            description="仅制裁事件",
            event_types=("trade_policy",),
            match_types=("product",),
            forced_level="P1",
            reason="测试",
            event_subtypes=("sanctions", "export_control"),
        )
        settings = ScoringSettings(forced_rules=(rule,))
        detail: dict[str, object] = {}

        level, score = apply_forced_rules(
            settings,
            "trade_policy",
            "product",
            "P3",
            55,
            detail,
            event_subtype="trade_tariff",
        )

        assert (level, score) == ("P3", 55)
        assert "forced_rule" not in detail

    def test_judicial_event_also_triggers(self) -> None:
        settings = ScoringSettings()
        detail: dict[str, object] = {}
        level, _ = apply_forced_rules(
            settings, "judicial", "registry_no", "P2", 70, detail
        )
        assert level == "P1"

    def test_no_forced_rules(self) -> None:
        settings = ScoringSettings(forced_rules=())
        detail: dict[str, object] = {}
        level, score = apply_forced_rules(
            settings, "compliance", "legal_name", "P3", 55, detail
        )
        assert level == "P3"
        assert score == 55

    def test_empty_event_types_matches_all(self) -> None:
        rule = ForcedRule(
            name="catch_all",
            description="匹配所有事件",
            event_types=(),
            match_types=("registry_no",),
            forced_level="P1",
            reason="测试",
        )
        settings = ScoringSettings(forced_rules=(rule,))
        detail: dict[str, object] = {}
        level, _ = apply_forced_rules(settings, "weather", "registry_no", "P4", 10, detail)
        assert level == "P1"


# ── 提醒自动失效单元测试 ──────────────────────────────────────


class TestComputeExpiresAt:
    def test_with_event_end_at(self) -> None:
        settings = ScoringSettings(alert_expiry_days=90)
        event = RiskEvent(
            dedup_key="test",
            event_type="weather",
            severity="high",
            summary="测试",
            end_at=datetime(2026, 8, 12, tzinfo=UTC),
            confidence=0.9,
            facts={},
        )
        expires = _compute_expires_at(event, settings)
        assert expires == datetime(2026, 8, 12, tzinfo=UTC) + timedelta(days=90)

    def test_without_event_end_at(self) -> None:
        settings = ScoringSettings(alert_expiry_days=30)
        event = RiskEvent(
            dedup_key="test",
            event_type="weather",
            severity="high",
            summary="测试",
            end_at=None,
            confidence=0.9,
            facts={},
        )
        before = datetime.now(UTC)
        expires = _compute_expires_at(event, settings)
        after = datetime.now(UTC)
        assert before + timedelta(days=30) <= expires <= after + timedelta(days=30)


# ── 集成测试：完整处理流程中的评分和失效 ─────────────────────


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


def _create_supplier(client: TestClient) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "supplier_code": "D7-TEST",
            "legal_name": "测试供应商有限公司",
            "country_code": "CN",
            "registry_no": "91310000D7TEST001",
            "aliases": [],
            "sites": [],
            "products": [],
        },
    )
    assert response.status_code == 201


def _import_signals(client: TestClient) -> None:
    document = {
        "version": "1.0",
        "signals": [
            {
                "external_id": "D7-SIGNAL-001",
                "title": "台风生产影响公告",
                "content": "受台风影响，测试供应商有限公司生产和物流活动暂停。",
                "url": "https://example.com/d7/001",
                "published_at": "2026-08-11T08:00:00+08:00",
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


def test_scoring_uses_v1_rule_version(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    assert str(alert.score_detail["rule_version"]).startswith("risk-score-v1-")


def test_alert_has_expires_at(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    assert alert.expires_at is not None
    # 事件 end_at = 2026-08-12，加 90 天
    assert alert.expires_at > datetime(2026, 8, 12, tzinfo=UTC)


def test_forced_rule_sanctions_compliance_entity_hit(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    result = SignalAnalysisResult(
        event_type="compliance",
        suggested_severity="medium",
        organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        locations=[],
        affected_activities=["compliance"],
        affected_products=[],
        summary_zh="测试供应商有限公司被列入制裁名单",
        evidence_sentences=["测试供应商有限公司被列入制裁名单。"],
        confidence=0.95,
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    assert alert.level == "P1"
    assert alert.score == 100
    forced = alert.score_detail.get("forced_rule")
    assert forced is not None
    assert forced["name"] == "sanctions_entity_hit"  # type: ignore[index]


def test_forced_rule_does_not_trigger_for_weather(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    assert "forced_rule" not in alert.score_detail


def test_alert_dedup_same_supplier_event(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """同一供应商+事件只保留一条当前提醒，重复处理更新而非新建。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    client.post(f"/api/v1/signals/{signal_id}/process")
    client.post(f"/api/v1/signals/{signal_id}/process")
    client.post(f"/api/v1/signals/{signal_id}/process")

    match_count = db_session.scalar(select(func.count()).select_from(SupplierEventMatch))
    alert_count = db_session.scalar(
        select(func.count()).select_from(RiskAlert).where(RiskAlert.status == "current")
    )
    assert match_count == 1
    assert alert_count == 1


def test_expire_alerts_marks_expired(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    client.post(f"/api/v1/signals/{signal_id}/process")
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    assert alert.status == "current"

    # 手动将 expires_at 设置为过去
    alert.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.flush()

    expired_count = expire_alerts(db_session)
    db_session.flush()
    assert expired_count == 1

    db_session.refresh(alert)
    assert alert.status == "expired"


def test_expire_endpoint(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    client.post(f"/api/v1/signals/{signal_id}/process")
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None

    # 未过期时返回 0
    response = client.post("/api/v1/risk-alerts/expire")
    assert response.status_code == 200
    assert response.json()["expired_count"] == 0

    # 手动设置过期后再调用
    alert.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()
    response = client.post("/api/v1/risk-alerts/expire")
    assert response.status_code == 200
    assert response.json()["expired_count"] == 1


def test_expired_alert_not_in_current_list(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    client.post(f"/api/v1/signals/{signal_id}/process")
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None

    # 当前列表中可见
    response = client.get("/api/v1/risk-alerts")
    assert response.json()["total"] == 1

    # 过期后不可见
    alert.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()
    client.post("/api/v1/risk-alerts/expire")
    response = client.get("/api/v1/risk-alerts")
    assert response.json()["total"] == 0

    # 但可以通过 status=expired 查看
    response = client.get("/api/v1/risk-alerts", params={"status": "expired"})
    assert response.json()["total"] == 1


def test_reprocess_expired_alert_restores_current(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """重新处理已失效的提醒时，恢复为 current 状态。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    client.post(f"/api/v1/signals/{signal_id}/process")
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None

    # 手动使其过期
    alert.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.flush()
    expire_alerts(db_session)
    db_session.flush()
    db_session.refresh(alert)
    assert alert.status == "expired"

    # 重新处理同一信号，恢复 current
    client.post(f"/api/v1/signals/{signal_id}/process")
    db_session.refresh(alert)
    assert alert.status == "current"
    assert alert.expires_at > datetime.now(UTC)
