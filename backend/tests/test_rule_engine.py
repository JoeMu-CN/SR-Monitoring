"""维度插件化规则引擎测试。

覆盖：维度按事件类型分派、宏观维度国家/行业匹配柱、维度专属强制规则、
热插拔（DB 启停维度）、热更新（DB 覆盖评分参数）、工作台维度与沙箱 API。
"""

import json

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import service as ai_service
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import RiskAlert, RuleDimensionConfig, SupplierEventMatch
from app.signals.models import RawSignal
from app.suppliers.models import Supplier, SupplierProduct, SupplierSite


class EngineProvider:
    provider_name = "engine-test"
    model = "engine-v1"

    def __init__(self, result: SignalAnalysisResult) -> None:
        self.result = result

    async def analyze_signal(self, _value: object) -> SignalAnalysisResult:
        return self.result


def _add_supplier(
    db_session: Session,
    *,
    code: str,
    name: str,
    registry_no: str | None = None,
    country: str = "CN",
    industry: str | None = None,
    raw_materials: list[str] | None = None,
    city: str | None = None,
    products: list[tuple[str, list[str]]] | None = None,
) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=name,
        country_code=country,
        registry_no=registry_no,
        industry=industry,
        raw_materials=raw_materials or [],
    )
    db_session.add(supplier)
    db_session.flush()
    if city:
        db_session.add(
            SupplierSite(
                supplier_id=supplier.id,
                site_name=city,
                country_code=country,
                region=None,
                city=city,
                address=f"{city}测试路1号",
            )
        )
    for product_name, keywords in products or []:
        db_session.add(
            SupplierProduct(supplier_id=supplier.id, name=product_name, keywords=keywords)
        )
    db_session.flush()
    return supplier


def _import_signal(client: TestClient, external_id: str, content: str) -> int:
    document = {
        "version": "1.0",
        "signals": [
            {
                "external_id": external_id,
                "title": external_id,
                "content": content,
                "url": "https://example.com/x",
                "published_at": "2026-08-11T08:00:00+08:00",
            }
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
    return external_id


def _run(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
    result: SignalAnalysisResult,
    external_id: str,
) -> list[RiskAlert]:
    monkeypatch.setattr(
        ai_service, "get_ai_provider", lambda _settings: EngineProvider(result)
    )
    _import_signal(client, external_id, result.summary_zh)
    signal_id = db_session.scalar(select(RawSignal.id).order_by(RawSignal.id))
    assert signal_id is not None
    response = client.post(f"/api/v1/signals/{signal_id}/process")
    assert response.status_code == 200
    return list(db_session.scalars(select(RiskAlert)))


def _result(**overrides: object) -> SignalAnalysisResult:
    base: dict[str, object] = {
        "event_type": "compliance",
        "event_subtype": "compliance_violation",
        "suggested_severity": "medium",
        "organizations": [],
        "locations": [],
        "affected_activities": ["operations"],
        "affected_products": [],
        "summary_zh": "测试事件",
        "evidence_sentences": ["测试事件证据。"],
        "confidence": 0.9,
    }
    base.update(overrides)
    return SignalAnalysisResult(**base)  # type: ignore[arg-type]


# ── 维度分派 ─────────────────────────────────────────────────────────


def test_dimension_dispatch_corporate(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(
        db_session, code="A1", name="测试供应商有限公司", registry_no="REG-A1"
    )
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="compliance",
            organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        ),
        "ENG-DISPATCH-1",
    )
    assert len(alerts) == 1
    assert alerts[0].score_detail["dimension"] == "corporate"


def test_dimension_dispatch_geopolitical(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(db_session, code="A2", name="地缘供应商", country="CN")
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="geopolitical",
            event_subtype="political_instability",
            locations=[{"name": "中国", "country_code": "CN"}],
        ),
        "ENG-DISPATCH-2",
    )
    assert alerts
    assert all(a.score_detail["dimension"] == "geopolitical" for a in alerts)


# ── 宏观匹配柱与强制规则 ──────────────────────────────────────────────


def test_geopolitical_country_only_is_weak_not_p1(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """单纯同国命中不得 P1（验收场景 4）：country 柱弱关联，不触发强制规则。"""
    _add_supplier(db_session, code="B1", name="同城供应商", country="CN", city="上海市")
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="geopolitical",
            event_subtype="political_instability",
            suggested_severity="low",
            locations=[{"name": "中国", "country_code": "CN"}],
        ),
        "ENG-GEO-COUNTRY",
    )
    assert alerts
    assert all(a.level == "P4" for a in alerts)
    match = db_session.scalar(select(SupplierEventMatch))
    assert match is not None
    assert "country" in match.match_type


def test_geopolitical_entity_hit_forces_p1(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(
        db_session, code="B2", name="受制裁供应商", registry_no="REG-B2"
    )
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="geopolitical",
            event_subtype="sanctions",
            organizations=[{"name": "受制裁供应商", "aliases": [], "registry_no": "REG-B2"}],
        ),
        "ENG-GEO-ENTITY",
    )
    assert len(alerts) == 1
    assert alerts[0].level == "P1"
    assert alerts[0].score_detail["forced_rule"]["name"] == "sanctions_geopolitical_entity_hit"


def test_economic_product_hit_forces_p1(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(
        db_session, code="B3", name="芯片供应商", products=[("高端芯片", ["芯片"])]
    )
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="trade_policy",
            event_subtype="export_control",
            affected_products=["高端芯片"],
        ),
        "ENG-ECO-PRODUCT",
    )
    assert len(alerts) == 1
    assert alerts[0].level == "P1"
    assert alerts[0].score_detail["forced_rule"]["name"] == "sanctions_product_hit"


def test_trade_tariff_product_hit_does_not_force_p1(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(
        db_session, code="B3-TARIFF", name="关税供应商", products=[("高端芯片", ["芯片"])]
    )
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="trade_policy",
            event_subtype="trade_tariff",
            affected_products=["高端芯片"],
        ),
        "ENG-ECO-TARIFF",
    )

    assert len(alerts) == 1
    assert alerts[0].level != "P1"
    assert "forced_rule" not in alerts[0].score_detail


def test_industry_column_matches_supplier_industry(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(db_session, code="B4", name="稀土依赖供应商", raw_materials=["稀土永磁"])
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="logistics",
            event_subtype="transport_disruption",
            affected_industries=["稀土永磁材料"],
        ),
        "ENG-IND-RAW",
    )
    assert len(alerts) == 1
    assert alerts[0].score_detail["dimension"] == "industry"


# ── 热插拔与热更新 ────────────────────────────────────────────────────


def test_disable_dimension_blocks_alerts(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """DB 禁用 corporate 维度后，compliance 事件不再产生提醒（热插拔）。"""
    _add_supplier(
        db_session, code="C1", name="测试供应商有限公司", registry_no="REG-C1"
    )
    db_session.add(
        RuleDimensionConfig(key="corporate", label="供应商主体", enabled=False, config={})
    )
    db_session.flush()
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="compliance",
            organizations=[{"name": "测试供应商有限公司", "aliases": []}],
        ),
        "ENG-DISABLE",
    )
    assert alerts == []


def test_db_override_severity_score_takes_effect(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """DB 覆盖维度评分参数即时生效（热更新）：把 high 严重性降到 1 分。"""
    _add_supplier(
        db_session, code="C2", name="测试供应商有限公司", registry_no="REG-C2", city="上海市"
    )
    db_session.add(
        RuleDimensionConfig(
            key="natural",
            label="自然环境",
            enabled=True,
            config={"severity_scores": {"high": 1}},
        )
    )
    db_session.flush()
    alerts = _run(
        client,
        db_session,
        monkeypatch,
        _result(
            event_type="weather",
            event_subtype="weather_alert",
            suggested_severity="high",
            organizations=[{"name": "测试供应商有限公司", "aliases": []}],
            locations=[{"name": "上海市", "country_code": "CN", "city": "上海市"}],
        ),
        "ENG-OVERRIDE",
    )
    assert alerts
    assert all(a.score_detail["severity"] == 1 for a in alerts)


def test_match_column_override_changes_rule_version(db_session: Session) -> None:
    from app.risks.engine.registry import load_dimensions

    before = next(d for d in load_dimensions(db_session) if d.key == "natural")
    db_session.add(
        RuleDimensionConfig(
            key="natural",
            label="自然环境",
            enabled=True,
            config={"match_columns": ["entity", "location"]},
        )
    )
    db_session.flush()
    after = next(d for d in load_dimensions(db_session) if d.key == "natural")

    assert after.scoring.rule_version != before.scoring.rule_version


# ── 工作台 API ────────────────────────────────────────────────────────


def test_dimensions_list_api(client: TestClient, db_session: Session) -> None:
    response = client.get("/api/v1/rule-engine/dimensions")
    assert response.status_code == 200
    items = response.json()
    keys = {item["key"] for item in items}
    assert keys == {
        "natural",
        "geopolitical",
        "economic",
        "policy",
        "industry",
        "corporate",
    }
    by_key = {item["key"]: item for item in items}
    assert by_key["policy"]["enabled"] is False
    assert by_key["corporate"]["enabled"] is True
    assert by_key["geopolitical"]["match_columns"] == [
        "entity",
        "location",
        "product",
        "country",
        "industry",
    ]


def test_dimension_toggle_api(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/rule-engine/dimensions/natural/toggle",
        json={"enabled": False},
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["has_override"] is True


def test_dimension_update_rejects_invalid_thresholds(client: TestClient) -> None:
    response = client.put(
        "/api/v1/rule-engine/dimensions/natural",
        json={"config": {"p1_min": 40, "p2_min": 65, "p3_min": 85}},
        headers={"X-User-Role": "admin"},
    )

    assert response.status_code == 422


def test_rule_change_preserves_previous_alert_revision(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    _add_supplier(
        db_session, code="REV-1", name="版本供应商", registry_no="REG-REV-1"
    )
    result = _result(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="high",
        organizations=[{"name": "版本供应商", "aliases": [], "registry_no": "REG-REV-1"}],
    )
    _run(client, db_session, monkeypatch, result, "ENG-REVISION")
    first = db_session.scalar(select(RiskAlert))
    assert first is not None
    first_version = first.score_detail["rule_version"]

    update = client.put(
        "/api/v1/rule-engine/dimensions/natural",
        json={"config": {"severity_scores": {"high": 1}}},
        headers={"X-User-Role": "admin"},
    )
    assert update.status_code == 200
    signal_id = db_session.scalar(select(RawSignal.id))
    assert signal_id is not None
    assert client.post(f"/api/v1/signals/{signal_id}/process").status_code == 200

    alerts = list(db_session.scalars(select(RiskAlert).order_by(RiskAlert.id)))
    assert len(alerts) == 2
    assert alerts[0].status == "expired"
    assert alerts[0].score_detail["rule_version"] == first_version
    assert alerts[1].status == "current"
    assert alerts[1].score_detail["rule_version"] != first_version


def test_sandbox_test_api(client: TestClient, db_session: Session) -> None:
    _add_supplier(
        db_session, code="D1", name="沙盘供应商", registry_no="REG-D1", city="上海市"
    )
    response = client.post(
        "/api/v1/rule-engine/test",
        json={
            "event_type": "compliance",
            "event_subtype": "sanctions",
            "severity": "high",
            "organizations": [{"name": "沙盘供应商", "aliases": [], "registry_no": "REG-D1"}],
            "locations": [{"name": "上海市", "country_code": "CN", "city": "上海市"}],
            "affected_products": [],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dimension"]["key"] == "corporate"
    assert payload["candidates"]
    assert payload["candidates"][0]["supplier_name"] == "沙盘供应商"
    assert payload["candidates"][0]["level"] == "P1"


def test_sandbox_without_subtype_is_not_forced_p1(
    client: TestClient, db_session: Session
) -> None:
    """未给出明确制裁子类型时，沙箱也不粗放升级为 P1（与真实处理链口径一致）。"""
    _add_supplier(
        db_session,
        code="D1-NOSUB",
        name="无子类型供应商",
        registry_no="REG-D1-NOSUB",
        city="上海市",
    )
    response = client.post(
        "/api/v1/rule-engine/test",
        json={
            "event_type": "compliance",
            "severity": "high",
            "organizations": [
                {"name": "无子类型供应商", "aliases": [], "registry_no": "REG-D1-NOSUB"}
            ],
            "locations": [{"name": "上海市", "country_code": "CN", "city": "上海市"}],
            "affected_products": [],
        },
    )
    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["level"] != "P1"
    assert candidate.get("forced_rule") is None


def test_dimension_api_exposes_content_and_source_status(client: TestClient) -> None:
    response = client.get("/api/v1/rule-engine/dimensions")
    assert response.status_code == 200
    dimensions = {item["key"]: item for item in response.json()}
    assert "天气与气象预警" in dimensions["natural"]["content_items"]
    nmc = next(
        s for s in dimensions["natural"]["data_sources"] if s["code"] == "nmc-weather"
    )
    assert nmc["name"] == "中央气象台"
    assert nmc["declared_status"] == "connected"
    tyc = next(
        s for s in dimensions["corporate"]["data_sources"] if s["code"] == "tianyancha"
    )
    assert tyc["declared_status"] == "external_tool"
    # policy 已按 19 个真实信源校正：声明 5 个已接入政策类信源（status=connected），
    # 但维度代码默认仍 event_types=() + enabled=False，靠 DB 启用后按信源归属接管。
    policy_sources = dimensions["policy"]["data_sources"]
    assert {s["code"] for s in policy_sources} == {
        "customs-announcement",
        "eu-official-journal",
        "eu-compliance",
        "uflpa-entity-list",
        "mee-announcement",
    }
    assert all(s["declared_status"] == "connected" for s in policy_sources)


def test_dimension_metadata_survives_database_override(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(RuleDimensionConfig(key="policy", label="政策与法规", enabled=True))
    db_session.flush()
    response = client.get("/api/v1/rule-engine/dimensions/policy")
    assert response.status_code == 200
    assert "行业监管" in response.json()["content_items"]
    assert response.json()["data_sources"]


def test_sandbox_geopolitical_country_weak(
    client: TestClient, db_session: Session
) -> None:
    _add_supplier(db_session, code="D2", name="沙箱同城", country="CN", city="上海市")
    response = client.post(
        "/api/v1/rule-engine/test",
        json={
            "event_type": "geopolitical",
            "severity": "low",
            "locations": [{"name": "中国", "country_code": "CN"}],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dimension"]["key"] == "geopolitical"
    assert payload["candidates"]
    assert all(c["level"] != "P1" for c in payload["candidates"])


# ── 维度事件类型互斥校验 ───────────────────────────────────────────────


def test_update_dimension_rejects_event_type_conflict_with_enabled_dimension(
    client: TestClient, db_session: Session
) -> None:
    """geopolitical 声明 weather 与已启用 natural 冲突 → 422 且不落库。"""
    response = client.put(
        "/api/v1/rule-engine/dimensions/geopolitical",
        json={"config": {"event_types": ["geopolitical", "weather"]}},
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["conflicts"] == [
        {"event_type": "weather", "dimension": "natural"}
    ]
    # 不落库：geopolitical 无 DB 覆盖行，natural 的覆盖行未被改动
    rows = {
        row.key: row
        for row in db_session.scalars(select(RuleDimensionConfig))
    }
    assert "geopolitical" not in rows
    assert "natural" not in rows


def test_update_dimension_allows_event_type_owned_by_disabled_dimension(
    client: TestClient, db_session: Session
) -> None:
    """weather 归属已停用 natural 时，geopolitical 可接管 → 200。"""
    db_session.add(
        RuleDimensionConfig(key="natural", label="自然环境", enabled=False)
    )
    db_session.flush()
    response = client.put(
        "/api/v1/rule-engine/dimensions/geopolitical",
        json={"config": {"event_types": ["geopolitical", "weather"]}},
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["event_types"] == ["geopolitical", "weather"]


def test_update_dimension_allows_event_type_reorder(
    client: TestClient, db_session: Session
) -> None:
    """仅调整顺序不产生冲突 → 200。"""
    response = client.put(
        "/api/v1/rule-engine/dimensions/natural",
        json={"config": {"event_types": ["geological", "weather"]}},
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["event_types"] == ["geological", "weather"]


def test_resolve_dimension_warns_on_duplicate_declaration(
    client: TestClient, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """存量重复声明时 resolve_dimension 记录 warning 且仍取第一个。"""
    from app.risks.engine.processing import resolve_dimension
    from app.risks.engine.registry import load_dimensions

    db_session.add(
        RuleDimensionConfig(
            key="geopolitical",
            label="地缘政治与安全",
            enabled=True,
            config={"event_types": ["weather"]},
        )
    )
    db_session.flush()
    with caplog.at_level("WARNING", logger="app.risks.engine.processing"):
        resolved = resolve_dimension(load_dimensions(db_session), "weather")
    assert resolved is not None
    assert resolved.key == "natural"  # 仍取第一个（natural 在默认顺序中居首）
    assert any(
        "weather" in record.message and "natural" in record.message
        for record in caplog.records
    )


def test_update_without_alert_expiry_days_preserves_existing_override(
    client: TestClient, db_session: Session
) -> None:
    """前端不再发送 alert_expiry_days 时，存量覆盖行的该键不被清除。

    对应任务 18：merged.update(payload.config.model_dump(exclude_none=True))
    只更新 payload 中非 None 的键；不含 alert_expiry_days 的 PUT 不会覆盖
    已存在的 alert_expiry_days=60。
    """
    db_session.add(
        RuleDimensionConfig(
            key="natural",
            label="自然环境",
            enabled=True,
            config={"alert_expiry_days": 60},
        )
    )
    db_session.flush()

    # 一次不含 alert_expiry_days 的 PUT（只改阈值）
    response = client.put(
        "/api/v1/rule-engine/dimensions/natural",
        json={"config": {"p1_min": 90}},
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["scoring"]["alert_expiry_days"] == 60

    # DB 覆盖行中该键仍为 60
    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == "natural")
    )
    assert row is not None
    assert row.config is not None
    assert row.config["alert_expiry_days"] == 60
