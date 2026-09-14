"""草稿配置解算预览接口测试（rule-engine-ui-revamp todo 2）。

覆盖：SandboxRequest 草稿字段与边界对齐、预览与保存同构（已存 DB 行覆盖 +
共享键级深合并函数）、全局草稿合并语义、草稿接管判定、占用冲突校验复用、
预览不写库、旧请求向后兼容。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.risks.models import RiskAlert, RuleDimensionConfig
from app.suppliers.models import Supplier, SupplierSite

TEST_URL = "/api/v1/rule-engine/test"
DIMENSION_URL = "/api/v1/rule-engine/dimensions"
GLOBAL_URL = "/api/v1/rule-engine/global-config"
GLOBAL_KEY = "__global_scoring__"


def _counts(db_session: Session) -> tuple[int, int]:
    """(规则配置行数, 提醒行数)：预览前后必须完全不变。"""
    rule_rows = db_session.scalar(select(func.count()).select_from(RuleDimensionConfig))
    alert_rows = db_session.scalar(select(func.count()).select_from(RiskAlert))
    return int(rule_rows or 0), int(alert_rows or 0)


def _add_supplier(
    db_session: Session,
    *,
    code: str,
    name: str,
    country: str = "CN",
    city: str | None = None,
) -> Supplier:
    supplier = Supplier(supplier_code=code, legal_name=name, country_code=country)
    db_session.add(supplier)
    db_session.flush()
    if city:
        db_session.add(
            SupplierSite(
                supplier_id=supplier.id,
                site_name=city,
                country_code=country,
                city=city,
                address=f"{city}测试路1号",
            )
        )
    db_session.flush()
    return supplier


def _organization(supplier: Supplier) -> dict[str, object]:
    """主体匹配载荷：仅法人全称（不提供注册号，稳定命中 legal_name）。"""
    return {"name": supplier.legal_name, "aliases": [], "registry_no": None}


def _post_test(
    client: TestClient, payload: dict[str, object], *, expect: int = 200
) -> dict[str, object]:
    response = client.post(TEST_URL, json=payload)
    assert response.status_code == expect, response.text
    return response.json()


def _first_candidate(payload: dict[str, object]) -> dict[str, object]:
    candidates = payload["candidates"]
    assert isinstance(candidates, list) and candidates, payload
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    return candidate


def _first_detail(payload: dict[str, object]) -> dict[str, object]:
    detail = _first_candidate(payload)["score_detail"]
    assert isinstance(detail, dict)
    return detail


# ── 预览与保存同构（含已存 DB 行覆盖与共享深合并） ─────────────────────


def test_preview_matches_saved_dimension_config_including_stored_overrides(
    client: TestClient, db_session: Session
) -> None:
    """预览必须与保存同构：已存行的 credibility_weight/p2_min 必须参与合并。

    负向对照：若实现退化为"代码默认 + draft"，source_credibility 会是
    80 * 0.2 = 16 而不是 80 * 0.5 = 40，本用例会失败。
    """
    db_session.add(
        RuleDimensionConfig(
            key="geopolitical",
            label="地缘政治与安全",
            enabled=True,
            config={"credibility_weight": 0.5, "p2_min": 55},
        )
    )
    db_session.flush()
    supplier = _add_supplier(db_session, code="PREV-1", name="预览供应商")

    sample: dict[str, object] = {
        "event_type": "geopolitical",
        "severity": "critical",
        "organizations": [_organization(supplier)],
        "credibility": 80,
    }
    draft = {"match_columns": ["entity"], "severity_scores": {"critical": 30}}

    before = _counts(db_session)
    preview = _post_test(
        client,
        {**sample, "dimension_key": "geopolitical", "draft_config": draft},
    )
    assert _counts(db_session) == before  # 预览不写任何行

    assert preview["dimension"]["key"] == "geopolitical"
    detail = _first_detail(preview)
    assert detail["source_credibility"] == 40
    assert detail["source_credibility"] != round(80 * 0.2)  # 负向对照
    assert detail["severity"] == 30
    candidate = _first_candidate(preview)
    assert candidate["score"] == 100
    assert candidate["level"] == "P1"

    # 真正保存同一草稿（键级深合并保留 credibility_weight/p2_min）
    saved = client.put(f"{DIMENSION_URL}/geopolitical", json={"config": draft})
    assert saved.status_code == 200, saved.text

    actual = _post_test(client, sample)  # 保存后不带草稿的旧请求
    assert actual["dimension"] == preview["dimension"]
    assert actual["candidates"] == preview["candidates"]


def test_preview_global_draft_deep_merges_with_stored_global_row(
    client: TestClient, db_session: Session
) -> None:
    """全局草稿与已存全局行键级深合并：critical=30 保留、high=20 覆盖。

    请求不带 dimension_key：全局草稿同样生效（resolve_dimension 自动判定
    接管维度）。
    """
    saved_global = client.put(GLOBAL_URL, json={"severity_scores": {"critical": 30}})
    assert saved_global.status_code == 200, saved_global.text
    supplier = _add_supplier(db_session, code="PREV-2", name="全局供应商")

    sample: dict[str, object] = {
        "event_type": "compliance",
        "organizations": [_organization(supplier)],
    }
    global_draft = {"severity_scores": {"high": 20}}

    before = _counts(db_session)
    preview_critical = _post_test(
        client, {**sample, "severity": "critical", "global_config": global_draft}
    )
    preview_high = _post_test(
        client, {**sample, "severity": "high", "global_config": global_draft}
    )
    assert _counts(db_session) == before

    assert _first_detail(preview_critical)["severity"] == 30
    assert _first_detail(preview_high)["severity"] == 20

    saved = client.put(GLOBAL_URL, json=global_draft)
    assert saved.status_code == 200, saved.text

    actual_critical = _post_test(client, {**sample, "severity": "critical"})
    actual_high = _post_test(client, {**sample, "severity": "high"})
    assert actual_critical["candidates"] == preview_critical["candidates"]
    assert actual_high["candidates"] == preview_high["candidates"]


def test_preview_country_only_draft_matches_saved_result(
    client: TestClient, db_session: Session
) -> None:
    """草稿 match_columns=[country] + critical=30：预览与保存后逐字段一致。"""
    _add_supplier(db_session, code="PREV-3", name="国家预览供应商")
    sample: dict[str, object] = {
        "event_type": "weather",
        "severity": "critical",
        "locations": [{"name": "中国", "country_code": "CN"}],
        "credibility": 80,
        "dimension_key": "natural",
    }
    draft = {"match_columns": ["country"], "severity_scores": {"critical": 30}}

    preview = _post_test(client, {**sample, "draft_config": draft})
    candidate = _first_candidate(preview)
    assert candidate["score_detail"]["level_cap"] == "country_only_max_p4"
    assert candidate["level"] == "P4"

    saved = client.put(f"{DIMENSION_URL}/natural", json={"config": draft})
    assert saved.status_code == 200, saved.text
    actual = _post_test(client, sample)
    assert actual["candidates"] == preview["candidates"]


# ── 草稿接管判定与占用冲突复用 ───────────────────────────────────────


def test_preview_event_type_removal_yields_no_dimension(
    client: TestClient, db_session: Session
) -> None:
    """草稿清空 geopolitical 事件类型后，该事件类型变为无维度接管。"""
    before = _counts(db_session)
    payload = _post_test(
        client,
        {
            "event_type": "geopolitical",
            "severity": "low",
            "dimension_key": "geopolitical",
            "draft_config": {"event_types": []},
        },
    )
    assert _counts(db_session) == before
    assert payload["dimension"] is None
    assert payload["candidates"] == []
    assert "geopolitical" in str(payload["message"])
    # 草稿未落库：该维度不存在持久化行
    assert (
        db_session.scalar(
            select(RuleDimensionConfig).where(RuleDimensionConfig.key == "geopolitical")
        )
        is None
    )


def test_preview_reuses_event_type_occupancy_conflict_validation(
    client: TestClient, db_session: Session
) -> None:
    """草稿把 weather 划给 geopolitical（已被 natural 占用）→ 与保存同样的 422。"""
    before = _counts(db_session)
    preview = client.post(
        TEST_URL,
        json={
            "event_type": "weather",
            "dimension_key": "geopolitical",
            "draft_config": {"event_types": ["geopolitical", "weather"]},
        },
    )
    assert preview.status_code == 422, preview.text
    assert _counts(db_session) == before

    save = client.put(
        f"{DIMENSION_URL}/geopolitical",
        json={"config": {"event_types": ["geopolitical", "weather"]}},
    )
    assert save.status_code == 422, save.text
    assert preview.json()["detail"] == save.json()["detail"]
    assert preview.json()["detail"]["conflicts"] == [
        {"event_type": "weather", "dimension": "natural"}
    ]


# ── 边界、错误输入与向后兼容 ────────────────────────────────────────


def test_preview_malformed_input_returns_4xx_without_writes(
    client: TestClient, db_session: Session
) -> None:
    """未知 dimension_key → 404；未知键的草稿 → 422；两者都不写库。"""
    before = _counts(db_session)

    unknown_dim = client.post(
        TEST_URL, json={"event_type": "weather", "dimension_key": "no-such-dimension"}
    )
    assert unknown_dim.status_code == 404
    assert unknown_dim.json()["detail"] == "维度不存在"

    unknown_draft = client.post(
        TEST_URL,
        json={
            "event_type": "weather",
            "dimension_key": "natural",
            "draft_config": {"unknown_key": 1},
        },
    )
    assert unknown_draft.status_code == 422

    unknown_global = client.post(
        TEST_URL,
        json={
            "event_type": "weather",
            "dimension_key": "natural",
            "global_config": {"unknown_key": 1},
        },
    )
    assert unknown_global.status_code == 422

    draft_without_key = client.post(
        TEST_URL,
        json={"event_type": "weather", "draft_config": {"match_columns": ["country"]}},
    )
    assert draft_without_key.status_code == 422

    assert _counts(db_session) == before


@pytest.mark.parametrize(
    "patch",
    [
        {"severity_scores": {"critical": 36}},
        {"association_scores": {"country": 31}},
        {"credibility_weight": 1.5},
        {"timeliness_with_date": 11},
        {"timeliness_without_date": -1},
        {"product_relevance_score": 6},
        {"alert_expiry_days": 0},
        {"alert_expiry_days": 3651},
    ],
)
def test_preview_draft_bounds_aligned_with_save(
    client: TestClient, db_session: Session, patch: dict[str, object]
) -> None:
    """draft_config 越界值与保存路径同样返回 422 且不写库。"""
    before = _counts(db_session)
    response = client.post(
        TEST_URL,
        json={"event_type": "weather", "dimension_key": "natural", "draft_config": patch},
    )
    assert response.status_code == 422, response.text
    assert _counts(db_session) == before


def test_preview_global_draft_bounds_aligned_with_save(
    client: TestClient, db_session: Session
) -> None:
    """global_config 越界值返回 422 且不写库。"""
    before = _counts(db_session)
    response = client.post(
        TEST_URL,
        json={
            "event_type": "weather",
            "global_config": {"severity_scores": {"critical": 36}},
        },
    )
    assert response.status_code == 422, response.text
    assert _counts(db_session) == before


def test_preview_legacy_request_shape_unchanged(
    client: TestClient, db_session: Session
) -> None:
    """未传任何草稿字段的旧请求仍返回原结构。"""
    supplier = _add_supplier(db_session, code="PREV-7", name="旧请求供应商")
    payload = _post_test(
        client,
        {
            "event_type": "compliance",
            "severity": "high",
            "organizations": [_organization(supplier)],
        },
    )
    assert set(payload) == {"dimension", "candidates"}
    assert payload["dimension"]["key"] == "corporate"
    assert _first_candidate(payload)["supplier_name"] == supplier.legal_name


def test_preview_lower_p1_threshold_upgrades_level(
    client: TestClient, db_session: Session
) -> None:
    """QA happy：草稿 p1_min=60 后，同一候选等级由 P2 变 P1（预览不写库）。"""
    supplier = _add_supplier(db_session, code="PREV-8", name="阈值供应商", city="上海市")
    sample: dict[str, object] = {
        "event_type": "weather",
        "severity": "medium",
        "organizations": [_organization(supplier)],
        "locations": [{"name": "上海市", "country_code": "CN", "city": "上海市"}],
        "credibility": 80,
        "dimension_key": "natural",
    }
    before = _counts(db_session)
    baseline = _post_test(client, sample)
    baseline_candidate = _first_candidate(baseline)
    assert baseline_candidate["score"] == 71
    assert baseline_candidate["level"] == "P2"

    preview = _post_test(client, {**sample, "draft_config": {"p1_min": 60}})
    assert _counts(db_session) == before
    preview_candidate = _first_candidate(preview)
    assert preview_candidate["score"] == 71
    assert preview_candidate["level"] == "P1"


# ── 手动 QA 转录：真实请求的原始 status+body 与落库计数（-s 运行） ────


def test_qa_transcript_preview_status_body_and_db_counts(
    client: TestClient, db_session: Session
) -> None:
    """手动 QA 渠道：打印带/不带草稿的原始响应与前后落库计数。"""
    supplier = _add_supplier(db_session, code="QA-1", name="QA 供应商")
    sample: dict[str, object] = {
        "event_type": "weather",
        "severity": "medium",
        "organizations": [_organization(supplier)],
        "dimension_key": "natural",
    }
    before = _counts(db_session)
    print(f"[QA] counts before (rule_dimension_configs, risk_alerts) = {before}")

    no_draft = client.post(TEST_URL, json={k: v for k, v in sample.items() if k != "dimension_key"})
    print(f"[QA] legacy(no draft) status={no_draft.status_code} body={no_draft.text}")

    with_draft = client.post(
        TEST_URL,
        json={**sample, "draft_config": {"p1_min": 60, "severity_scores": {"medium": 21}}},
    )
    print(f"[QA] draft status={with_draft.status_code} body={with_draft.text}")

    after = _counts(db_session)
    print(f"[QA] counts after (rule_dimension_configs, risk_alerts) = {after}")
    assert before == after
