"""全局评分与强制规则配置层测试（rule-engine-ui-revamp todo 1）。

覆盖：五层合成顺序（代码默认 → 全局行 → 维度增量 → 维度追加 → 维度 DB 行）、
强制规则整体替换（全局行含 forced_rules 键时跳过维度追加）、键级深合并
（全局 PUT 与维度 PUT 均发部分 diff）、全部校验边界、确认门（含 DELETE 与
维度 PUT 旁路）、审计、GET 全局层口径与遮蔽披露、RISK_SCORING_CONFIG 一致性。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import SecurityAuditEvent
from app.risks.models import RuleDimensionConfig
from app.risks.scoring import ForcedRule
from app.suppliers.models import Supplier, SupplierProduct

GLOBAL_KEY = "__global_scoring__"
URL = "/api/v1/rule-engine/global-config"
DIMENSION_URL = "/api/v1/rule-engine/dimensions"


def _global_row(db_session: Session) -> RuleDimensionConfig | None:
    return db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == GLOBAL_KEY)
    )


def _add_disabled_global_row(
    db_session: Session, config: dict[str, object]
) -> RuleDimensionConfig:
    """插入一条停用的全局配置行（模拟历史遗留/人工停用状态）。"""
    row = RuleDimensionConfig(
        key=GLOBAL_KEY, label="全局评分配置", enabled=False, config=config
    )
    db_session.add(row)
    db_session.flush()
    return row


def _rule(name: str, **overrides: object) -> dict[str, object]:
    """构造一条最小合法强制规则载荷。"""
    rule: dict[str, object] = {
        "name": name,
        "description": f"{name} 说明",
        "event_types": ["compliance"],
        "event_subtypes": [],
        "match_types": ["legal_name"],
        "forced_level": "P1",
        "reason": f"{name} 原因",
    }
    rule.update(overrides)
    return rule


def _confirm_params(confirm: bool) -> dict[str, str] | None:
    return {"confirm_disable_forced_rules": "true"} if confirm else None


def _put(
    client: TestClient,
    payload: dict[str, object],
    *,
    confirm: bool = False,
    expect: int = 200,
) -> dict[str, object]:
    response = client.put(URL, json=payload, params=_confirm_params(confirm))
    assert response.status_code == expect, response.text
    return response.json()


def _delete(client: TestClient, *, confirm: bool = False, expect: int = 200) -> dict[str, object]:
    response = client.delete(URL, params=_confirm_params(confirm))
    assert response.status_code == expect, response.text
    return response.json()


def _names(rules: list[dict[str, object]]) -> list[str]:
    return [str(rule["name"]) for rule in rules]


def _dimension_rules(db_session: Session) -> dict[str, list[str]]:
    from app.risks.engine.registry import load_dimensions

    return {
        dim.key: [rule.name for rule in dim.scoring.forced_rules]
        for dim in load_dimensions(db_session)
    }


def _add_supplier(db_session: Session, *, code: str, name: str, registry_no: str) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=name,
        country_code="CN",
        registry_no=registry_no,
        raw_materials=["稀土永磁"],
    )
    db_session.add(supplier)
    db_session.flush()
    db_session.add(
        SupplierProduct(supplier_id=supplier.id, name="高端芯片", keywords=["芯片"])
    )
    db_session.flush()
    return supplier


def _test_signature(payload: dict[str, object]) -> dict[object, tuple[object, object, object]]:
    """候选按 supplier_id 对齐，签名只含 level/score/forced_rule 名称。"""
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    signature: dict[object, tuple[object, object, object]] = {}
    for candidate in candidates:
        assert isinstance(candidate, dict)
        detail = candidate["score_detail"]
        assert isinstance(detail, dict)
        forced = detail.get("forced_rule")
        forced_name = forced.get("name") if isinstance(forced, dict) else None
        signature[candidate["supplier_id"]] = (
            candidate["level"],
            candidate["score"],
            forced_name,
        )
    return signature


# ── 五层合成与键级深合并 ─────────────────────────────────────────────


def test_put_severity_critical_configured_and_synthesized(
    client: TestClient, db_session: Session
) -> None:
    """PUT 全局 severity critical=30：GET 生效且 source=configured，各维度合成生效。"""
    body = _put(client, {"severity_scores": {"critical": 30}})
    assert body["source"] == "configured"
    assert body["effective"]["severity_scores"]["critical"] == 30

    get = client.get(URL)
    assert get.status_code == 200
    assert get.json()["source"] == "configured"
    assert get.json()["effective"]["severity_scores"]["critical"] == 30

    from app.risks.engine.registry import load_dimensions

    for dim in load_dimensions(db_session):
        assert dim.scoring.severity_scores["critical"] == 30


def test_partial_put_preserves_existing_forced_rules(
    client: TestClient, db_session: Session
) -> None:
    """仅 PUT p1_min 时，已存的 forced_rules 不被清空（合并语义）。"""
    defaults = client.get(URL).json()["defaults"]["forced_rules"]
    _put(client, {"forced_rules": defaults, "p1_min": 88})
    _put(client, {"p1_min": 90})

    row = _global_row(db_session)
    assert row is not None
    assert _names(row.config["forced_rules"]) == _names(defaults)


def test_global_put_deep_merges_score_dicts(client: TestClient, db_session: Session) -> None:
    """连续两次部分 PUT severity（critical 再 high）后两个键都保留。"""
    _put(client, {"severity_scores": {"critical": 30}})
    _put(client, {"severity_scores": {"high": 20}})

    row = _global_row(db_session)
    assert row is not None
    assert row.config["severity_scores"] == {"critical": 30, "high": 20}

    effective = client.get(URL).json()["effective"]["severity_scores"]
    assert effective["critical"] == 30
    assert effective["high"] == 20
    assert effective["medium"] == 20  # 未覆盖键保留默认


def test_dimension_put_deep_merges_score_dicts(client: TestClient, db_session: Session) -> None:
    """维度 PUT 同样键级深合并：critical=30 后再 high=20，两者都保留。"""
    first = client.put(
        f"{DIMENSION_URL}/natural",
        json={"config": {"severity_scores": {"critical": 30}}},
    )
    assert first.status_code == 200
    second = client.put(
        f"{DIMENSION_URL}/natural",
        json={"config": {"severity_scores": {"high": 20}}},
    )
    assert second.status_code == 200

    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == "natural")
    )
    assert row is not None
    assert row.config["severity_scores"] == {"critical": 30, "high": 20}

    from app.risks.engine.registry import load_dimensions

    natural = next(d for d in load_dimensions(db_session) if d.key == "natural")
    assert natural.scoring.severity_scores["critical"] == 30
    assert natural.scoring.severity_scores["high"] == 20


def test_partial_put_keeps_dimension_forced_rule_appends(
    client: TestClient, db_session: Session
) -> None:
    """仅 PUT p1_min（不含 forced_rules 键）不触发整体替换，维度追加仍生效。"""
    _put(client, {"p1_min": 90})

    rules = _dimension_rules(db_session)
    assert {"sanctions_entity_hit", "sanctions_geopolitical_entity_hit"} <= set(
        rules["geopolitical"]
    )
    assert {"sanctions_entity_hit", "sanctions_product_hit"} <= set(rules["economic"])
    assert "policy_industry_hit" in rules["policy"]


# ── 强制规则替换与确认门 ─────────────────────────────────────────────


def test_put_dropping_forced_rules_requires_confirm(
    client: TestClient, db_session: Session
) -> None:
    """保留 sanctions_entity_hit 但删掉维度追加两条 → 无确认 422（全部规则均安全关键）。"""
    defaults = client.get(URL).json()["defaults"]["forced_rules"]
    keep = [rule for rule in defaults if rule["name"] == "sanctions_entity_hit"]

    response = client.put(URL, json={"forced_rules": keep})
    assert response.status_code == 422
    assert _global_row(db_session) is None


def test_put_forced_rules_replaces_and_skips_dimension_appends(
    client: TestClient, db_session: Session
) -> None:
    """全局行含 forced_rules 时整体替换：geopolitical 生效列表恰好等于该列表。"""
    body = _put(client, {"forced_rules": [_rule("only_entity_hit")]}, confirm=True)
    assert _names(body["effective"]["forced_rules"]) == ["only_entity_hit"]

    rules = _dimension_rules(db_session)
    assert rules["geopolitical"] == ["only_entity_hit"]
    assert rules["economic"] == ["only_entity_hit"]
    assert rules["natural"] == ["only_entity_hit"]


def test_put_empty_forced_rules_requires_confirm_then_clears_all(
    client: TestClient, db_session: Session
) -> None:
    """空列表语义：无确认 422 且配置不变；带确认则全部维度强制规则清空。"""
    response = client.put(URL, json={"forced_rules": []})
    assert response.status_code == 422
    assert _global_row(db_session) is None

    body = _put(client, {"forced_rules": []}, confirm=True)
    assert body["effective"]["forced_rules"] == []
    rules = _dimension_rules(db_session)
    assert all(names == [] for names in rules.values())


def test_put_custom_rule_without_entity_hit_requires_confirm(
    client: TestClient, db_session: Session
) -> None:
    """写入不含 sanctions_entity_hit 的自定义列表，无确认同样 422。"""
    response = client.put(URL, json={"forced_rules": [_rule("only_x")]})
    assert response.status_code == 422
    assert _global_row(db_session) is None


def test_delete_restores_code_defaults(client: TestClient, db_session: Session) -> None:
    """DELETE 后回退代码默认：source=default，geopolitical 恢复两条强制规则。"""
    _put(client, {"forced_rules": [_rule("only_entity_hit")]}, confirm=True)

    body = _delete(client, confirm=True)
    assert body["source"] == "default"
    assert _global_row(db_session) is None

    rules = _dimension_rules(db_session)
    assert "sanctions_entity_hit" in rules["geopolitical"]
    assert "sanctions_geopolitical_entity_hit" in rules["geopolitical"]


def test_delete_removing_custom_rule_requires_confirm(
    client: TestClient, db_session: Session
) -> None:
    """DELETE 会移除当前生效规则时，无确认 422 且行不变；带确认才删除。"""
    _put(client, {"forced_rules": [_rule("only_x")]}, confirm=True)

    response = client.delete(URL)
    assert response.status_code == 422
    assert _global_row(db_session) is not None

    body = _delete(client, confirm=True)
    assert body["source"] == "default"
    assert _global_row(db_session) is None


def test_delete_after_subset_write_needs_no_confirm(
    client: TestClient, db_session: Session
) -> None:
    """先写入默认集合子集，再 DELETE：差集为空 → 无需确认即可删除并回退默认。"""
    defaults = client.get(URL).json()["defaults"]["forced_rules"]
    subset = [rule for rule in defaults if rule["name"] == "sanctions_entity_hit"]
    _put(client, {"forced_rules": subset}, confirm=True)

    body = _delete(client)
    assert body["source"] == "default"
    assert _global_row(db_session) is None


def test_dimension_put_removing_forced_rules_requires_confirm(
    client: TestClient, db_session: Session
) -> None:
    """维度 PUT 的 forced_rules 旁路同样受确认门保护。"""
    response = client.put(
        f"{DIMENSION_URL}/natural",
        json={"config": {"forced_rules": []}},
    )
    assert response.status_code == 422
    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == "natural")
    )
    assert row is None

    confirmed = client.put(
        f"{DIMENSION_URL}/natural",
        json={"config": {"forced_rules": []}},
        params={"confirm_disable_forced_rules": "true"},
    )
    assert confirmed.status_code == 200
    row = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == "natural")
    )
    assert row is not None
    assert row.config == {"forced_rules": []}


def test_confirm_flag_never_stored_in_config(client: TestClient, db_session: Session) -> None:
    """确认标志仅查询参数：不得进入 row.config；放进请求体则 extra=forbid 拒绝。"""
    _put(client, {"forced_rules": []}, confirm=True)
    row = _global_row(db_session)
    assert row is not None
    assert "confirm_disable_forced_rules" not in row.config

    rejected = client.put(
        URL,
        json={"confirm_disable_forced_rules": True, "p1_min": 90},
    )
    assert rejected.status_code == 422
    db_session.refresh(row)
    assert "p1_min" not in row.config


# ── 校验边界 ─────────────────────────────────────────────────────────


def test_put_invalid_threshold_order_rejected(client: TestClient, db_session: Session) -> None:
    """p1_min=60, p2_min=70 违反 p1 > p2 > p3 → 422 且不落库。"""
    response = client.put(URL, json={"p1_min": 60, "p2_min": 70})
    assert response.status_code == 422
    assert _global_row(db_session) is None


def test_put_out_of_range_severity_rejected(client: TestClient, db_session: Session) -> None:
    """severity critical=40 越界（>35）→ 422 且不落库。"""
    response = client.put(URL, json={"severity_scores": {"critical": 40}})
    assert response.status_code == 422
    assert _global_row(db_session) is None


def test_put_rejects_invalid_rule_fields(client: TestClient, db_session: Session) -> None:
    """强制规则非法字段：未知匹配类型、非法等级、空名称、重复名称 → 全部 422。"""
    cases: list[dict[str, object]] = [
        {"strong_match_types": ["not_a_match_type"]},
        {"forced_rules": [_rule("bad_level", forced_level="P9")]},
        {"forced_rules": [_rule("bad_type", match_types=["not_a_match_type"])]},
        {"forced_rules": [_rule("")]},
        {"forced_rules": [_rule("dup"), _rule("dup")]},
    ]
    for payload in cases:
        response = client.put(URL, json=payload)
        assert response.status_code == 422, payload
    assert _global_row(db_session) is None


def test_put_rejects_unknown_keys(client: TestClient, db_session: Session) -> None:
    """GlobalScoringPatch 使用 extra=forbid：未知键 422 且不落库。"""
    response = client.put(URL, json={"unknown_key": 1})
    assert response.status_code == 422
    assert _global_row(db_session) is None


# ── 审计 ─────────────────────────────────────────────────────────────


def test_global_put_writes_audit_with_actor(client: TestClient, db_session: Session) -> None:
    """成功 PUT 写 action=rule_global_config_update 审计且 actor_user_id 非空。"""
    _put(client, {"severity_scores": {"critical": 30}})

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "rule_global_config_update"
            )
        )
    )
    assert len(events) == 1
    assert events[0].actor_user_id is not None


def test_delete_writes_audit_with_actor(client: TestClient, db_session: Session) -> None:
    """DELETE 写 action=rule_global_config_delete 审计且 actor_user_id 非空。"""
    _put(client, {"p1_min": 90})
    _delete(client)

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "rule_global_config_delete"
            )
        )
    )
    assert len(events) == 1
    assert events[0].actor_user_id is not None


def test_dimension_put_writes_audit(client: TestClient, db_session: Session) -> None:
    """维度 PUT 写 forced_rules 时产生 action=rule_dimension_update 审计。"""
    response = client.put(
        f"{DIMENSION_URL}/natural",
        json={"config": {"forced_rules": []}},
        params={"confirm_disable_forced_rules": "true"},
    )
    assert response.status_code == 200

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "rule_dimension_update"
            )
        )
    )
    assert len(events) == 1
    assert events[0].actor_user_id is not None


# ── GET 全局层口径与遮蔽披露 ─────────────────────────────────────────


def test_global_association_merge_and_shadowed_by(
    client: TestClient, db_session: Session
) -> None:
    """全局 country=15 下 natural 生效 15，geopolitical 被维度增量覆盖为 8。"""
    _put(client, {"association_scores": {"country": 15}})

    from app.risks.engine.registry import load_dimensions

    dims = {dim.key: dim for dim in load_dimensions(db_session)}
    assert dims["natural"].scoring.association_scores["country"] == 15
    assert dims["geopolitical"].scoring.association_scores["country"] == 8

    body = client.get(URL).json()
    assert body["effective"]["association_scores"]["country"] == 15
    assert set(body["shadowed_by"]["country"]) >= {"geopolitical", "economic", "policy"}


def test_get_default_forced_rules_excludes_policy_rule(client: TestClient) -> None:
    """GET 默认含三条强制规则、不含 policy_industry_hit；被排除规则有披露。"""
    body = client.get(URL).json()
    names = _names(body["defaults"]["forced_rules"])
    assert names == [
        "sanctions_entity_hit",
        "sanctions_geopolitical_entity_hit",
        "sanctions_product_hit",
    ]
    assert body["source"] == "default"

    dropped = body["dropped_dimension_rules"]
    assert [rule["name"] for rule in dropped] == ["policy_industry_hit"]
    assert dropped[0]["dimension"] == "policy"


def test_get_only_returns_global_layer(client: TestClient) -> None:
    """GET 只返回全局层：维度增量 country=8 不得当作全局层值返回。"""
    _put(client, {"association_scores": {"country": 15}})

    body = client.get(URL).json()
    assert body["effective"]["association_scores"]["country"] == 15
    # geopolitical/economic/policy 的维度增量为 country=8，不得混进全局层
    assert body["effective"]["association_scores"]["country"] != 8
    assert "country" not in body["defaults"]["association_scores"]


def test_disabled_global_row_falls_back_to_defaults(
    client: TestClient, db_session: Session
) -> None:
    """全局行 enabled=False 时回退代码默认（读取与合成均忽略）。"""
    db_session.add(
        RuleDimensionConfig(
            key=GLOBAL_KEY,
            label="全局评分配置",
            enabled=False,
            config={"severity_scores": {"critical": 1}},
        )
    )
    db_session.flush()

    body = client.get(URL).json()
    assert body["source"] == "default"
    assert body["effective"]["severity_scores"]["critical"] == 35

    from app.risks.engine.registry import load_dimensions

    assert all(dim.scoring.severity_scores["critical"] == 35 for dim in load_dimensions(db_session))


# ── 默认列表原样落库的行为等价（含 /test 前后一致） ──────────────────


def test_put_default_forced_rules_behavior_equivalent(
    client: TestClient, db_session: Session
) -> None:
    """对默认强制规则做原样 PUT：按行为等价断言各维度可见规则集合不变。"""
    defaults = client.get(URL).json()["defaults"]["forced_rules"]

    from app.risks.engine.registry import load_dimensions

    before: dict[str, tuple[tuple[ForcedRule, ...], set[str]]] = {}
    for dim in load_dimensions(db_session):
        before[dim.key] = (dim.scoring.forced_rules, set(dim.config.event_types))
    _put(client, {"forced_rules": defaults})
    after: dict[str, tuple[ForcedRule, ...]] = {}
    for dim in load_dimensions(db_session):
        after[dim.key] = dim.scoring.forced_rules

    def visible(rules: tuple[ForcedRule, ...], event_types: set[str]) -> set[str]:
        """只保留 event_types 与该维度有交集的规则（policy 无交集 → 空集）。"""
        return {rule.name for rule in rules if set(rule.event_types) & event_types}

    for key in ("natural", "geopolitical", "economic", "industry", "corporate"):
        before_rules, before_types = before[key]
        assert visible(before_rules, before_types) == visible(after[key], before_types), key
    # policy 例外：policy_industry_hit 因维度未声明事件类型被排除
    assert "policy_industry_hit" in {rule.name for rule in before["policy"][0]}
    assert "policy_industry_hit" not in {rule.name for rule in after["policy"]}


def test_sandbox_results_unchanged_after_default_forced_rules_put(
    client: TestClient, db_session: Session
) -> None:
    """5 个有事件类型的维度：原样 PUT 默认前后 /test 的 level/score/forced_rule 一致。"""
    supplier = _add_supplier(
        db_session,
        code="GLOBAL-QA-1",
        name="全局QA供应商",
        registry_no="REG-GLOBAL-QA-1",
    )
    organization = {
        "name": supplier.legal_name,
        "aliases": [],
        "registry_no": supplier.registry_no,
    }
    samples: dict[str, dict[str, object]] = {
        "natural": {
            "event_type": "weather",
            "event_subtype": "weather_alert",
            "severity": "high",
            "organizations": [organization],
        },
        "geopolitical": {
            "event_type": "geopolitical",
            "event_subtype": "sanctions",
            "organizations": [organization],
        },
        "economic": {
            "event_type": "trade_policy",
            "event_subtype": "export_control",
            "affected_products": ["高端芯片"],
        },
        "industry": {
            "event_type": "logistics",
            "event_subtype": "transport_disruption",
            "affected_industries": ["稀土永磁材料"],
        },
        "corporate": {
            "event_type": "compliance",
            "event_subtype": "compliance_violation",
            "organizations": [organization],
        },
    }

    before: dict[str, dict[str, object]] = {}
    for key, sample in samples.items():
        response = client.post("/api/v1/rule-engine/test", json=sample)
        assert response.status_code == 200, response.text
        before[key] = response.json()
        assert before[key]["dimension"]["key"] == key, key
        assert before[key]["candidates"], key

    defaults = client.get(URL).json()["defaults"]["forced_rules"]
    _put(client, {"forced_rules": defaults})

    for key, sample in samples.items():
        response = client.post("/api/v1/rule-engine/test", json=sample)
        assert response.status_code == 200, response.text
        after = response.json()
        assert before[key]["dimension"] == after["dimension"], key
        assert _test_signature(before[key]) == _test_signature(after), key


# ── RISK_SCORING_CONFIG 一致性 ───────────────────────────────────────


def test_risk_scoring_config_env_consistent_get_delete_restore(
    client: TestClient, db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    """环境变量自定义 forced_rules 时，GET 默认、DELETE 与恢复默认三者一致。"""
    custom = _rule("env_custom_hit")
    monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps({"forced_rules": [custom]}))
    expected = ["env_custom_hit", "sanctions_geopolitical_entity_hit", "sanctions_product_hit"]

    defaults = client.get(URL).json()["defaults"]["forced_rules"]
    assert _names(defaults) == expected

    _put(client, {"forced_rules": defaults})
    assert _names(client.get(URL).json()["effective"]["forced_rules"]) == expected

    deleted = _delete(client)
    assert deleted["source"] == "default"
    assert _names(deleted["defaults"]["forced_rules"]) == expected

    _put(client, {"forced_rules": defaults})
    assert _names(client.get(URL).json()["effective"]["forced_rules"]) == expected


# ── 权限与 CSRF ──────────────────────────────────────────────────────


def test_global_config_permissions(
    client: TestClient,
    db_session: Session,
    auth_as: Callable[[str, str], object],
) -> None:
    """GET 需 rule_summary_view；写需 rule_manage + CSRF。"""
    auth_as("viewer", "viewer-global-config")
    assert client.get(URL).status_code == 200
    assert client.put(URL, json={"p1_min": 90}).status_code == 403
    assert client.delete(URL).status_code == 403

    auth_as("risk_admin", "admin-global-config")
    client.headers.pop("X-CSRF-Token")
    assert client.put(URL, json={"p1_min": 90}).status_code == 403
    assert client.delete(URL).status_code == 403
    assert _global_row(db_session) is None


# ── 修复轮次：停用行确认门、阈值基线、列表替换、遮蔽口径与名单去重 ──


def test_disabled_row_reenabled_by_plain_put_requires_confirm(
    client: TestClient, db_session: Session
) -> None:
    """缺陷回归：停用行存 forced_rules=[] 时，仅 PUT p1_min 也会清空默认强制规则。

    修复后语义：PUT 总会启用行，确认门必须按"操作起始状态 vs 结果状态"的强制
    规则差集判断；无确认 422 且行保持停用未变，带确认才落库且 [] 被原样保留。
    """
    _add_disabled_global_row(db_session, {"forced_rules": []})

    response = client.put(URL, json={"p1_min": 90})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert set(detail["removed"]) == {
        "sanctions_entity_hit",
        "sanctions_geopolitical_entity_hit",
        "sanctions_product_hit",
    }
    db_session.expire_all()
    row = _global_row(db_session)
    assert row is not None
    assert row.enabled is False
    assert row.config == {"forced_rules": []}

    body = _put(client, {"p1_min": 90}, confirm=True)
    db_session.expire_all()
    row = _global_row(db_session)
    assert row is not None
    assert row.enabled is True
    assert row.config == {"forced_rules": [], "p1_min": 90}
    assert body["effective"]["forced_rules"] == []


def test_disabled_row_legacy_invalid_thresholds_rejected_on_enable(
    client: TestClient, db_session: Session
) -> None:
    """停用行的遗留非法阈值不能借 PUT 其它字段静默启用：按 stored ∪ patch 校验。"""
    _add_disabled_global_row(db_session, {"p1_min": 60, "p2_min": 70})

    response = client.put(URL, json={"severity_scores": {"high": 20}})
    assert response.status_code == 422, response.text
    db_session.expire_all()
    row = _global_row(db_session)
    assert row is not None
    assert row.enabled is False
    assert row.config == {"p1_min": 60, "p2_min": 70}


def test_disabled_row_without_forced_rules_key_enables_without_confirm(
    client: TestClient, db_session: Session
) -> None:
    """正常路径回归：停用行未存 forced_rules 键时启用+改参数无需确认，不误报。"""
    _add_disabled_global_row(db_session, {"severity_scores": {"critical": 30}})

    body = _put(client, {"p1_min": 90})
    row = _global_row(db_session)
    assert row is not None
    assert row.enabled is True
    assert "forced_rules" not in row.config
    assert body["source"] == "configured"
    assert body["effective"]["severity_scores"]["critical"] == 30
    assert body["effective"]["p1_min"] == 90


def test_strong_match_types_whole_list_replacement(
    client: TestClient, db_session: Session
) -> None:
    """strong_match_types 为整体替换语义：先 [product] 再 [country] → 只留 country。"""
    _put(client, {"strong_match_types": ["product"]})
    body = _put(client, {"strong_match_types": ["country"]})

    row = _global_row(db_session)
    assert row is not None
    assert row.config["strong_match_types"] == ["country"]
    assert body["effective"]["strong_match_types"] == ["country"]


def test_dimension_db_row_overrides_global_and_static_overrides(
    client: TestClient, db_session: Session
) -> None:
    """第五层（维度 DB 行）最终覆盖全局行与维度静态 scoring_overrides。"""
    _put(client, {"association_scores": {"country": 15}})
    db_session.add(
        RuleDimensionConfig(
            key="geopolitical",
            label="地缘政治",
            enabled=True,
            config={"association_scores": {"country": 99}},
        )
    )
    db_session.flush()

    from app.risks.engine.registry import load_dimensions

    dims = {dim.key: dim for dim in load_dimensions(db_session)}
    assert dims["geopolitical"].scoring.association_scores["country"] == 99
    assert dims["natural"].scoring.association_scores["country"] == 15
    # 全局层账面值不受维度 DB 行影响（GET 只返回全局层）
    assert client.get(URL).json()["effective"]["association_scores"]["country"] == 15


def test_forced_rules_shadowed_by_only_declared_dimensions(
    client: TestClient, db_session: Session
) -> None:
    """forced_rules_shadowed_by 只含 default_dimensions() 声明的维度，排除同表无关行。"""
    db_session.add(
        RuleDimensionConfig(
            key="natural",
            label="自然灾害（测试）",
            enabled=False,
            config={"forced_rules": [_rule("natural_extra")]},
        )
    )
    db_session.add(
        RuleDimensionConfig(
            key="signal-filter",
            label="信号过滤（非维度）",
            enabled=True,
            config={"forced_rules": [_rule("filter_extra")]},
        )
    )
    db_session.flush()

    body = client.get(URL).json()
    assert body["forced_rules_shadowed_by"] == ["natural"]


def test_global_forced_rule_defaults_dedupe_name_collision(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    """代码/环境默认与维度 forced_rules_add 同名时按名称去重，代码默认胜出。"""
    env_rule = _rule("sanctions_product_hit", description="环境变量覆盖版")
    monkeypatch.setenv("RISK_SCORING_CONFIG", json.dumps({"forced_rules": [env_rule]}))

    from app.risks.engine.registry import global_forced_rule_defaults

    defaults = global_forced_rule_defaults()
    assert [rule.name for rule in defaults.rules] == [
        "sanctions_product_hit",
        "sanctions_geopolitical_entity_hit",
    ]
    assert defaults.rules[0].description == "环境变量覆盖版"
    assert [entry["name"] for entry in defaults.dropped] == ["policy_industry_hit"]

    # 去重后的默认列表可直接原样 PUT 回存（PUT 拒绝重复名称）。
    body = _put(
        client, {"forced_rules": client.get(URL).json()["defaults"]["forced_rules"]}
    )
    assert _names(body["effective"]["forced_rules"]) == [
        "sanctions_product_hit",
        "sanctions_geopolitical_entity_hit",
    ]
