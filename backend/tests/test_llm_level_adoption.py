"""Todo 12：规则引擎按独立阈值采纳 LLM 建议等级（计划验收七条 + 附加合同）。

覆盖：
① 强制规则 P1 + LLM P4 → 仍 P1（强制规则不可被 LLM 绕过）；
② 强匹配 base P1 + LLM P2 → 仍 P1（LLM 只能确认或提升，不能降级 base）；
③ confidence=0.74 < θ=0.75 → 未采纳，回落 base；
④ 弱关联 cap P2 + LLM P1（≥θ）→ 仍 P2（上限在采纳之后，不可绕过）；
⑤ 无供应商匹配 → 不产生 alert，建议仅保留在 AIAnalysisRecord；
⑥ 缺 suggested_level 的历史分析行为与旧链一致；
⑦ 自定义 p1_min=90 时 score floor 取运行时阈值 90（不写死 85）。

附加合同：score_detail 恒含完整审计键（deterministic_level/llm_level/llm_confidence/
llm_adopted/llm_theta/llm_rationale/capped_level/final_level）；采纳阈值 θ 为独立配置
（默认 0.75，与 AI review 0.65 解耦），环境变量与工作台均校验 0..1 且往返一致。

执行环境：Compose 隔离测试栈，真实 PostgreSQL；AI 走确定性 StaticProvider，零外网。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import RiskAlert
from app.risks.scoring import (
    LEVEL_RANK,
    LlmSuggestion,
    ScoringSettings,
    compute_level,
    load_scoring_settings,
    resolve_level,
)
from app.signals.models import RawSignal

GLOBAL_CONFIG_URL = "/api/v1/rule-engine/global-config"
DIMENSION_CONFIG_URL = "/api/v1/rule-engine/dimensions/corporate"

# 任务书要求的完整审计键集合：无论是否采纳都必须存在。
AUDIT_KEYS = (
    "deterministic_level",
    "llm_level",
    "llm_confidence",
    "llm_adopted",
    "llm_theta",
    "llm_rationale",
    "capped_level",
    "final_level",
)


def _settings(**overrides: object) -> ScoringSettings:
    return ScoringSettings(**overrides)


# ── 评分链单元测试（七条验收的纯函数部分） ────────────────────────


class TestResolveLevelChain:
    def test_forced_rule_p1_beats_llm_p4(self) -> None:
        """后置 强制规则（合规制裁记录）→ 强制 P1，压过 LLM 建议 P4。"""
        settings = _settings()
        detail: dict[str, object] = {}
        level, score = resolve_level(
            settings,
            LlmSuggestion(suggested_level="P4", confidence=0.99, rationale="证据充分"),
            "compliance",
            "legal_name",
            55,
            detail,
            event_subtype="sanctions",
        )
        assert (level, score) == ("P1", 100)
        assert detail["deterministic_level"] == "P3"
        assert detail["llm_adopted"] is True
        assert detail["final_level"] == "P1"
        forced = detail["forced_rule"]
        assert isinstance(forced, dict)
        assert forced["name"] == "sanctions_entity_hit"

    def test_llm_cannot_downgrade_base_p1(self) -> None:
        """② base 已是 P1 时，采纳的 LLM P2 只能确认，绝不降级。"""
        settings = _settings()
        detail: dict[str, object] = {}
        level, score = resolve_level(
            settings,
            LlmSuggestion(suggested_level="P2", confidence=0.95),
            "weather",
            "registry_no",
            90,
            detail,
        )
        assert level == "P1"
        assert score == 90
        assert detail["deterministic_level"] == "P1"
        assert detail["final_level"] == "P1"
        assert detail["llm_adopted"] is True

    def test_confidence_below_theta_falls_back_to_base(self) -> None:
        """③ 0.74 < θ=0.75：建议被忽略，score/level 与 base 完全一致。"""
        settings = _settings()
        detail: dict[str, object] = {}
        level, score = resolve_level(
            settings,
            LlmSuggestion(suggested_level="P1", confidence=0.74),
            "weather",
            "legal_name",
            70,
            detail,
        )
        assert (level, score) == ("P2", 70)
        assert detail["llm_adopted"] is False
        assert detail["llm_level"] == "P1"
        assert detail["llm_confidence"] == 0.74
        assert detail["deterministic_level"] == "P2"
        assert detail["final_level"] == "P2"

    def test_weak_association_cap_beats_adopted_llm_p1(self) -> None:
        """④ 弱关联上限在采纳之后：建议 P1 被 cap 到 P2，且 floor 使用 p2_min。"""
        settings = _settings()
        detail: dict[str, object] = {}
        level, score = resolve_level(
            settings,
            LlmSuggestion(suggested_level="P1", confidence=0.9),
            "weather",
            "site_distance",
            50,
            detail,
        )
        assert level == "P2"
        assert score == settings.p2_min == 65
        assert detail["llm_adopted"] is True
        assert detail["capped_level"] == "P2"
        assert detail["final_level"] == "P2"
        assert detail["level_cap"] == "weak_association_max_p2"

    def test_missing_suggestion_matches_legacy_chain(self) -> None:
        """⑥ 历史分析无 suggested_level：与改动前 compute_level 链完全一致。"""
        settings = _settings()
        for score in (0, 39, 40, 64, 65, 84, 85, 100):
            detail: dict[str, object] = {}
            level, result_score = resolve_level(
                settings,
                LlmSuggestion(),
                "weather",
                "legal_name",
                score,
                detail,
            )
            assert level == compute_level(settings, score)
            assert result_score == score
            assert detail["llm_level"] is None
            assert detail["llm_adopted"] is False

    def test_custom_p1_min_floor_uses_runtime_threshold(self) -> None:
        """⑦ 运行时 p1_min=90：采纳提升到 P1 时 floor 取 90 而非写死的 85。"""
        settings = _settings(p1_min=90)
        detail: dict[str, object] = {}
        level, score = resolve_level(
            settings,
            LlmSuggestion(suggested_level="P1", confidence=0.9),
            "weather",
            "legal_name",
            80,
            detail,
        )
        assert detail["deterministic_level"] == "P2"
        assert level == "P1"
        assert score == 90
        assert score != 85

    def test_audit_keys_always_present(self) -> None:
        """score_detail 恒含完整审计键（采纳与未采纳两种路径）。"""
        settings = _settings()
        for suggestion in (
            LlmSuggestion(suggested_level="P2", confidence=0.9, rationale="理由"),
            LlmSuggestion(suggested_level="P1", confidence=0.1),
            LlmSuggestion(),
        ):
            detail: dict[str, object] = {}
            resolve_level(settings, suggestion, "weather", "legal_name", 70, detail)
            assert set(AUDIT_KEYS) <= set(detail)
            assert detail["llm_theta"] == 0.75

    def test_theta_boundary_is_inclusive(self) -> None:
        """confidence 恰好等于 θ 时采纳（>= 语义）。"""
        settings = _settings()
        detail: dict[str, object] = {}
        level, _ = resolve_level(
            settings,
            LlmSuggestion(suggested_level="P1", confidence=0.75),
            "weather",
            "legal_name",
            50,
            detail,
        )
        assert detail["llm_adopted"] is True
        assert level == "P1"

    def test_level_rank_order_is_explicit(self) -> None:
        """显式等级序：P1 最严（1）到 P4 最松（4）。"""
        assert LEVEL_RANK == {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


# ── θ 独立配置：默认 0.75 与 0..1 校验 ───────────────────────────


class TestThresholdConfig:
    def test_default_threshold_is_0_75(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.delenv("RISK_SCORING_CONFIG", raising=False)
        settings = load_scoring_settings()
        assert settings.llm_adopt_threshold == 0.75
        assert settings.llm_adopt_threshold != 0.65

    def test_env_override(self, monkeypatch: MonkeyPatch) -> None:
        config = {"llm_adopt_threshold": 0.6}
        monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps(config))
        settings = load_scoring_settings()
        assert settings.llm_adopt_threshold == 0.6

    def test_invalid_env_values_fall_back_to_default(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        for raw in (1.5, -0.1, "0.8", None, True):
            monkeypatch.setenv(
                "RISK_SCORING_CONFIG", json.dumps({"llm_adopt_threshold": raw})
            )
            settings = load_scoring_settings()
            assert settings.llm_adopt_threshold == 0.75, raw


# ── 工作台往返与 0..1 校验 ───────────────────────────────────────


class TestWorkbenchThreshold:
    def test_global_config_roundtrip(self, client: TestClient) -> None:
        body = client.get(GLOBAL_CONFIG_URL).json()
        assert body["defaults"]["llm_adopt_threshold"] == 0.75
        assert body["effective"]["llm_adopt_threshold"] == 0.75

        response = client.put(GLOBAL_CONFIG_URL, json={"llm_adopt_threshold": 0.9})
        assert response.status_code == 200
        assert response.json()["effective"]["llm_adopt_threshold"] == 0.9

        again = client.get(GLOBAL_CONFIG_URL).json()
        assert again["effective"]["llm_adopt_threshold"] == 0.9

    def test_global_config_rejects_out_of_range(self, client: TestClient) -> None:
        assert (
            client.put(GLOBAL_CONFIG_URL, json={"llm_adopt_threshold": 1.5}).status_code
            == 422
        )
        assert (
            client.put(
                GLOBAL_CONFIG_URL, json={"llm_adopt_threshold": -0.01}
            ).status_code
            == 422
        )

    def test_dimension_patch_rejects_out_of_range(self, client: TestClient) -> None:
        response = client.put(
            DIMENSION_CONFIG_URL,
            json={"config": {"llm_adopt_threshold": 1.5}},
        )
        assert response.status_code == 422

    def test_dimension_summary_exposes_threshold(self, client: TestClient) -> None:
        body = client.get(DIMENSION_CONFIG_URL).json()
        assert body["scoring"]["llm_adopt_threshold"] == 0.75


# ── 端到端：处理接口中的采纳与无匹配行为 ────────────────────────


class StaticProvider:
    provider_name = "static-test"
    model = "static-v1"

    def __init__(self, result: SignalAnalysisResult) -> None:
        self.calls = 0
        self.result = result

    async def analyze_signal(self, _value: object) -> SignalAnalysisResult:
        self.calls += 1
        return self.result


def _import_signal(client: TestClient, *, external_id: str) -> None:
    document = {
        "version": "1.0",
        "signals": [
            {
                "external_id": external_id,
                "title": "台风生产影响公告",
                "content": "受台风影响，测试供应商有限公司生产和物流活动暂停。",
                "url": "https://example.com/t12/001",
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


def _create_supplier(client: TestClient) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "supplier_code": "T12-TEST",
            "legal_name": "测试供应商有限公司",
            "country_code": "CN",
            "registry_no": "91310000T12TEST001",
            "aliases": [],
            "sites": [],
            "products": [],
        },
    )
    assert response.status_code == 201


def _latest_signal_id(db_session: Session) -> int:
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None
    return signal_id


def test_no_match_leaves_suggestion_in_analysis_only(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """⑤ 无供应商匹配：不产生 alert，建议只保留在 AIAnalysisRecord。"""
    result = SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="high",
        organizations=[],
        locations=[{"name": "上海市", "country_code": "CN", "city": "上海市"}],
        affected_activities=["production", "logistics"],
        affected_products=[],
        summary_zh="台风影响上海地区生产和物流",
        evidence_sentences=["受台风影响，上海地区部分生产和物流活动暂停。"],
        confidence=0.9,
        suggested_level="P1",
        level_rationale="证据充分，建议 P1",
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _import_signal(client, external_id="T12-NO-MATCH")

    response = client.post(f"/api/v1/signals/{_latest_signal_id(db_session)}/process")

    assert response.status_code == 200
    assert response.json()["alert_ids"] == []
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0
    analysis = db_session.scalar(select(AIAnalysisRecord))
    assert analysis is not None
    assert analysis.needs_review is True
    assert "未匹配到供应商" in (analysis.review_reason or "")
    assert analysis.result is not None
    assert analysis.result["suggested_level"] == "P1"
    assert analysis.result["level_rationale"] == "证据充分，建议 P1"


def test_adopted_level_raises_alert_and_floor_uses_runtime_threshold(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """端到端：采纳提升等级，floor 使用运行时 p1_min=90，审计键完整落库。"""
    monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps({"p1_min": 90}))
    result = SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="low",
        organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        locations=[],
        affected_activities=["production"],
        affected_products=[],
        summary_zh="台风影响测试供应商",
        evidence_sentences=["受台风影响，测试供应商有限公司生产活动暂停。"],
        confidence=0.92,
        suggested_level="P1",
        level_rationale="主体直接受影响，建议 P1",
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signal(client, external_id="T12-ADOPTED")

    response = client.post(f"/api/v1/signals/{_latest_signal_id(db_session)}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    detail = alert.score_detail
    assert alert.level == "P1"
    # low=10 + legal_name=25 + credibility(50*0.2=10) + timeliness=10 = 55 → base P3；
    # 采纳 P1 后 floor 取运行时 p1_min=90（而非默认 85）。
    assert alert.score == 90
    assert detail["deterministic_level"] == "P3"
    assert detail["llm_level"] == "P1"
    assert detail["llm_adopted"] is True
    assert detail["llm_theta"] == 0.75
    assert detail["llm_confidence"] == 0.92
    assert detail["llm_rationale"] == "主体直接受影响，建议 P1"
    assert detail["capped_level"] == "P1"
    assert detail["final_level"] == "P1"
    assert set(AUDIT_KEYS) <= set(detail)


def test_unadopted_low_confidence_keeps_base_behavior(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """未达阈值时端到端与旧行为一致：不改动 score/level，审计键标记未采纳。"""
    result = SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="low",
        organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        locations=[],
        affected_activities=["production"],
        affected_products=[],
        summary_zh="台风影响测试供应商",
        evidence_sentences=["受台风影响，测试供应商有限公司生产活动暂停。"],
        confidence=0.5,
        suggested_level="P1",
    )
    provider = StaticProvider(result)
    monkeypatch.setattr(ai_service, "get_ai_provider", lambda _settings: provider)
    _create_supplier(client)
    _import_signal(client, external_id="T12-NOT-ADOPTED")

    response = client.post(f"/api/v1/signals/{_latest_signal_id(db_session)}/process")

    assert response.status_code == 200
    alert = db_session.scalar(select(RiskAlert))
    assert alert is not None
    detail = alert.score_detail
    # 55 分 → P3，建议 P1 因 confidence=0.5 < 0.75 未被采纳。
    assert alert.level == "P3"
    assert alert.score == 55
    assert detail["llm_adopted"] is False
    assert detail["deterministic_level"] == "P3"
    assert detail["final_level"] == "P3"
