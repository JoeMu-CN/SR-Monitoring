"""D7 可配置评分、强制规则、提醒去重和自动失效的测试。"""

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from risk_validity_fixtures import SignalSpec, linked_risk
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
from app.risks.validity import refresh_event_support
from app.signals.models import RawSignal

NOW_UTC = datetime(2026, 9, 6, 12, tzinfo=UTC)

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
    def test_uses_event_valid_until_instead_of_event_end_at(self) -> None:
        settings = ScoringSettings(alert_expiry_days=90)
        valid_until = datetime(2026, 8, 15, tzinfo=UTC)
        event = RiskEvent(
            dedup_key="test",
            event_type="weather",
            severity="high",
            summary="测试",
            end_at=datetime(2026, 8, 12, tzinfo=UTC),
            valid_until=valid_until,
            confidence=0.9,
            facts={},
        )
        expires = _compute_expires_at(event, settings)
        assert expires == valid_until

    def test_without_validity_deadline_is_unbounded(self) -> None:
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
        expires = _compute_expires_at(event, settings)
        assert expires is None


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
            event_subtype="weather_alert",
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


def _mark_supporting_signal_expired(db_session: Session, signal_id: int) -> None:
    signal = db_session.get(RawSignal, signal_id)
    assert signal is not None
    signal.validity_state = "expired"
    signal.valid_until = datetime.now(UTC) - timedelta(days=1)
    signal.validity_reason = {
        "code": "expired_by_test",
        "anchor_source": "published_at",
        "details": {},
    }
    db_session.flush()


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
        event_subtype="sanctions",
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

    _mark_supporting_signal_expired(db_session, signal_id)

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

    # 关联证据失效后再调用
    _mark_supporting_signal_expired(db_session, signal_id)
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
    _mark_supporting_signal_expired(db_session, signal_id)
    client.post("/api/v1/risk-alerts/expire")
    response = client.get("/api/v1/risk-alerts")
    assert response.json()["total"] == 0

    # 但可以通过 status=expired 查看
    response = client.get("/api/v1/risk-alerts", params={"status": "expired"})
    assert response.json()["total"] == 1


def test_reprocess_expired_alert_without_effective_evidence_is_rejected(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """无有效证据时，重新处理不得把已失效提醒恢复为 current。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    client.post(f"/api/v1/signals/{signal_id}/process")
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None

    _mark_supporting_signal_expired(db_session, signal_id)
    expire_alerts(db_session)
    db_session.flush()
    db_session.refresh(alert)
    assert alert.status == "expired"

    response = client.post(f"/api/v1/signals/{signal_id}/process")

    assert response.status_code == 409
    db_session.refresh(alert)
    assert alert.status == "expired"


# ── 任务 8：事件/提醒到期物化的读路径与有效期边界 ────────────────


def _api_datetime(value: str | None) -> datetime | None:
    """把 pydantic v2 的 UTC 'Z' 序列化归一为可比较的 datetime。"""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_validity_fields_materialized_on_first_processing_and_exposed_by_read_apis(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """事件首次创建即同事务物化有效期列；读接口原样暴露只读字段。"""
    provider = StaticProvider()
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signals(client)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None

    response = client.post(f"/api/v1/signals/{signal_id}/process")
    assert response.status_code == 200

    event = db_session.scalar(select(RiskEvent))
    assert event is not None
    # 首次处理在同一事务物化，而不是等后续 refresh job 才补列。
    assert event.validity_state == "active"
    assert event.valid_until is not None
    assert event.validity_policy_version is not None
    assert set(event.validity_reason) == {"code", "anchor_source", "details"}
    assert "content" not in str(event.validity_reason)

    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    assert alert.expires_at == event.valid_until
    assert alert.expiry_kind == "finite"

    listed = client.get("/api/v1/risk-alerts").json()["items"][0]
    assert _api_datetime(listed["expires_at"]) == alert.expires_at
    assert listed["expiry_kind"] == "finite"
    assert listed["validity_state"] == "active"
    assert _api_datetime(listed["valid_until"]) == event.valid_until
    assert _api_datetime(listed["review_due_at"]) == event.review_due_at
    assert listed["validity_policy_version"] == event.validity_policy_version
    assert listed["validity_reason"] == event.validity_reason

    detail = client.get(f"/api/v1/events/{event.id}").json()
    assert detail["validity_state"] == "active"
    assert _api_datetime(detail["valid_until"]) == event.valid_until
    assert _api_datetime(detail["review_due_at"]) == event.review_due_at
    assert detail["validity_policy_version"] == event.validity_policy_version
    assert detail["validity_reason"] == event.validity_reason
    assert set(detail["validity_reason"]) == {"code", "anchor_source", "details"}


def test_expiry_boundary_equal_now_expires_finite_alert(db_session: Session) -> None:
    """expires_at == now_utc 即失效（<= 边界），> now_utc 保持 current。"""
    due = linked_risk(
        db_session,
        (SignalSpec("boundary-equal", valid_until=NOW_UTC),),
        now_utc=NOW_UTC,
    )
    future = linked_risk(
        db_session,
        (SignalSpec("boundary-future", valid_until=NOW_UTC + timedelta(days=1)),),
        now_utc=NOW_UTC,
    )

    expired_count = expire_alerts(db_session, now_utc=NOW_UTC)

    assert expired_count == 1
    assert due.event.validity_state == "expired"
    assert due.alert.status == "expired"
    assert future.event.validity_state == "active"
    assert future.alert.status == "current"
    assert future.alert.expires_at == NOW_UTC + timedelta(days=1)
    assert future.alert.expiry_kind == "finite"


def test_event_validity_unbounded_with_infinite_evidence_falls_back_on_revoke(
    db_session: Session,
) -> None:
    """任一无限证据使事件截止为 NULL；逐条撤销后回落到剩余最晚截止。"""
    day_3 = NOW_UTC + timedelta(days=3)
    day_7 = NOW_UTC + timedelta(days=7)
    risk = linked_risk(
        db_session,
        (
            SignalSpec("validity-three-days", valid_until=day_3),
            SignalSpec("validity-seven-days", valid_until=day_7),
            SignalSpec("validity-infinite", mode="until_revoked"),
        ),
        now_utc=NOW_UTC,
    )

    # until_revoked 证据 valid_until/review_due_at 均为 NULL → 事件/提醒 unbounded。
    assert expire_alerts(db_session, now_utc=NOW_UTC) == 0
    assert risk.event.valid_until is None
    assert risk.event.review_due_at is None
    assert risk.alert.expires_at is None
    assert risk.alert.expiry_kind == "unbounded"

    # 撤销无限证据 → 回落到第 7 天。
    risk.signals[2].validity_state = "revoked"
    expire_alerts(db_session, now_utc=NOW_UTC)
    assert risk.event.valid_until == day_7
    assert risk.alert.expires_at == day_7
    assert risk.alert.expiry_kind == "finite"

    # 再撤销较长证据 → 回落到第 3 天。
    risk.signals[1].validity_state = "revoked"
    expire_alerts(db_session, now_utc=NOW_UTC)
    assert risk.event.valid_until == day_3
    assert risk.alert.expires_at == day_3
    assert risk.event.validity_state == "active"


def test_review_due_confirm_extends_deadline_and_revoke_terminates_alert(
    db_session: Session,
) -> None:
    """法规 confirm 延长复核日，revoke 立即终止；提醒在同一协调调用内跟随。"""
    day_3 = NOW_UTC + timedelta(days=3)
    day_7 = NOW_UTC + timedelta(days=7)
    risk = linked_risk(
        db_session,
        (SignalSpec("validity-review", review_due_at=day_3, mode="until_revoked"),),
        now_utc=NOW_UTC,
    )
    # until_revoked 仅带复核日 → 事件/提醒截止即复核日（finite）。
    assert expire_alerts(db_session, now_utc=NOW_UTC) == 0
    assert risk.event.valid_until == day_3
    assert risk.alert.expires_at == day_3
    assert risk.alert.expiry_kind == "finite"
    assert risk.alert.status == "current"

    # confirm 把复核日延长到 day_7 → 事件与提醒在同一次协调调用内投影到 day_7。
    risk.signals[0].review_due_at = day_7
    assert expire_alerts(db_session, now_utc=NOW_UTC) == 0
    assert risk.event.review_due_at == day_7
    assert risk.event.valid_until == day_7
    assert risk.alert.expires_at == day_7
    assert risk.alert.expiry_kind == "finite"
    assert risk.alert.status == "current"

    # revoke 后无剩余有效证据 → 同一 expire_alerts 调用内事件与提醒 expired。
    risk.signals[0].validity_state = "revoked"
    assert expire_alerts(db_session, now_utc=NOW_UTC) == 1
    assert risk.event.validity_state == "expired"
    assert risk.alert.status == "expired"


def test_validity_non_effective_evidence_never_keeps_current_alert(
    db_session: Session,
) -> None:
    """只有 pending/过期/复核到期证据时，不产生 current 提醒且不再恢复。"""
    specs = (
        SignalSpec("validity-pending", state="pending_classification"),
        SignalSpec("validity-due", review_due_at=NOW_UTC, mode="until_revoked"),
        SignalSpec("validity-expired", state="expired", valid_until=NOW_UTC),
    )
    for spec in specs:
        risk = linked_risk(db_session, (spec,), now_utc=NOW_UTC)
        assert expire_alerts(db_session, now_utc=NOW_UTC) == 1
        assert risk.event.validity_state == "expired"
        assert risk.alert.status == "expired"
        # 更晚时钟再次协调不会复活提醒，也不会滚动截止。
        assert expire_alerts(db_session, now_utc=NOW_UTC + timedelta(days=1)) == 0
        assert risk.alert.status == "expired"


def test_validity_materialized_deadline_does_not_drift_across_calls(
    db_session: Session,
) -> None:
    """end_at 在过去或为 None 且被重复处理时，截止时间不随调用时刻漂移。"""
    day_3 = NOW_UTC + timedelta(days=3)
    risk = linked_risk(
        db_session,
        (SignalSpec("validity-no-end", valid_until=day_3),),
        now_utc=NOW_UTC,
    )
    risk.event.end_at = NOW_UTC - timedelta(days=1)
    db_session.flush()

    first = refresh_event_support(db_session, risk.event, now_utc=NOW_UTC)
    assert first.expires_at == day_3

    risk.event.end_at = None
    db_session.flush()
    later = refresh_event_support(
        db_session, risk.event, now_utc=NOW_UTC + timedelta(days=2)
    )
    assert later.expires_at == day_3
    assert risk.event.valid_until == day_3
