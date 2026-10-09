"""信源优先（source-first）维度路由测试。

锁定 19 个真实信源 code 与维度的确定性归属，并证明实际 ``process_event`` 按
信源 code 路由（而非仅 helper）：policy 维度保持 ``event_types=()`` +
``enabled=False`` 代码默认，仅靠 DB 启用即可接管其声明信源，无需新增 AI 事件类型。

覆盖：
- 19 个 code 全部按 owner 路由，且不受 AI 粗事件类型影响；
- policy 未启用时已知 owner 返回 None 且禁止 event_type 回退；
- 未声明 code / 无 source_code 保留旧 event_type 逻辑；
- 同一 code 被多维度重复声明时安全阻断并给出可诊断原因；
- 政策信号经真实 process_event 得到 policy 维度评分；
- 维度 API 如实暴露声明信源与 inputs 覆盖。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import LocationReference, SignalAnalysisResult
from app.risks.engine.config import DimensionDataSource
from app.risks.engine.processing import (
    process_event,
    resolve_dimension,
)
from app.risks.engine.registry import load_dimensions
from app.risks.models import RiskAlert, RuleDimensionConfig
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)

# 已定映射：19 个真实 code → 唯一 owner 维度。
SOURCE_OWNER: dict[str, str] = {
    "nmc-weather": "natural",
    "usgs-earthquake-day": "natural",
    "mem-incident-bulletin": "natural",
    "ofac-sdn": "geopolitical",
    "bis-entity-list": "geopolitical",
    "mofcom-entity-detail": "geopolitical",
    "fmprc-press": "geopolitical",
    "commodity-futures": "economic",
    "fx-rates": "economic",
    "pbc-lpr": "economic",
    "stats-pmi": "economic",
    "wto-news": "economic",
    "customs-announcement": "policy",
    "eu-official-journal": "policy",
    "eu-compliance": "policy",
    "uflpa-entity-list": "policy",
    "mee-announcement": "policy",
    "sse-shipping": "industry",
    "tianyancha": "corporate",
}

EXPECTED_OWNERS: dict[str, list[str]] = {
    "natural": ["nmc-weather", "usgs-earthquake-day", "mem-incident-bulletin"],
    "geopolitical": [
        "ofac-sdn",
        "bis-entity-list",
        "mofcom-entity-detail",
        "fmprc-press",
    ],
    "economic": [
        "commodity-futures",
        "fx-rates",
        "pbc-lpr",
        "stats-pmi",
        "wto-news",
    ],
    "policy": [
        "customs-announcement",
        "eu-official-journal",
        "eu-compliance",
        "uflpa-entity-list",
        "mee-announcement",
    ],
    "industry": ["sse-shipping"],
    "corporate": ["tianyancha"],
}

POLICY_CODES = frozenset(EXPECTED_OWNERS["policy"])


def _enable_policy(session: Session) -> None:
    session.add(
        RuleDimensionConfig(key="policy", label="政策与法规", enabled=True)
    )
    session.flush()


def _source(session: Session, code: str, *, enabled: bool = True) -> DataSource:
    existing = session.scalar(select(DataSource).where(DataSource.code == code))
    if existing is not None:
        existing.enabled = enabled
        session.flush()
        return existing
    source = DataSource(
        code=code,
        name=code,
        source_type="pull",
        credibility=80,
        enabled=enabled,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(source)
    session.flush()
    return source


def _active_signal(session: Session, source: DataSource, external_id: str) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=external_id,
        title=f"测试信号-{external_id}",
        content="政策公告测试内容",
        published_at=NOW,
        collected_at=NOW,
        fingerprint=f"fp-{external_id}",
        raw_data={},
        validity_profile="compliance_violation",
        validity_state="active",
        valid_from=NOW,
        valid_until=NOW + timedelta(days=365),
        review_due_at=None,
        validity_mode="fixed_days",
        lifecycle_action="assert",
        validity_policy_version="source-routing-test-v1",
        validity_reason={
            "code": "classification_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


def _analysis(
    session: Session, signal: RawSignal, result: SignalAnalysisResult
) -> AIAnalysisRecord:
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="static-test",
        model="static-v1",
        prompt_version="signal-analysis-v3",
        status="succeeded",
        started_at=signal.collected_at,
        finished_at=signal.collected_at,
        result=result.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return record


def _policy_result() -> SignalAnalysisResult:
    """粗事件类型 compliance（默认路由到 corporate）的合规类事件。"""
    return SignalAnalysisResult(
        event_type="compliance",
        event_subtype="compliance_violation",
        suggested_severity="medium",
        organizations=[],
        locations=[LocationReference(name="中国", country_code="CN")],
        affected_activities=["operations"],
        affected_products=[],
        summary_zh="海关政策公告测试",
        evidence_sentences=["政策公告测试证据。"],
        confidence=0.8,
    )


def _add_supplier(session: Session, code: str, name: str) -> Supplier:
    supplier = Supplier(supplier_code=code, legal_name=name, country_code="CN")
    session.add(supplier)
    session.flush()
    return supplier


# ── 信源优先路由合同 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source_code", "owner"), sorted(SOURCE_OWNER.items())
)
def test_source_first_routing_ignores_ai_event_type(
    db_session: Session, source_code: str, owner: str
) -> None:
    """19 个 code 均按 owner 路由；探针事件类型指向其它维度也不受影响。"""
    _enable_policy(db_session)
    dimensions = load_dimensions(db_session)
    # natural 的 owner 用 compliance 作探针（会命中 corporate），其余用 weather。
    probe_event_type = "compliance" if owner == "natural" else "weather"

    resolved = resolve_dimension(
        dimensions, probe_event_type, source_code=source_code
    )

    assert resolved is not None, source_code
    assert resolved.key == owner


def test_policy_disabled_no_event_type_fallback(db_session: Session) -> None:
    """已知 owner 被禁用时返回 None，且不得回退到 event_type（policy 默认禁用）。"""
    dimensions = load_dimensions(db_session)
    policy = next(d for d in dimensions if d.key == "policy")
    assert policy.enabled is False

    resolved = resolve_dimension(
        dimensions, "compliance", source_code="customs-announcement"
    )
    assert resolved is None

    from app.risks.engine.processing import resolve_dimension_reason

    reason = resolve_dimension_reason(
        dimensions, "compliance", "customs-announcement"
    )
    assert "customs-announcement" in reason
    assert "policy" in reason


def test_unknown_or_absent_source_keeps_event_type_logic(
    db_session: Session,
) -> None:
    """未声明 code 或无 source_code 时保留旧 event_type 逻辑。"""
    dimensions = load_dimensions(db_session)

    resolved = resolve_dimension(dimensions, "weather")
    assert resolved is not None
    assert resolved.key == "natural"

    resolved = resolve_dimension(dimensions, "weather", source_code="not-declared")
    assert resolved is not None
    assert resolved.key == "natural"

    resolved = resolve_dimension(dimensions, "compliance", source_code=None)
    assert resolved is not None
    assert resolved.key == "corporate"


def test_duplicate_source_declaration_blocks_routing(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """同一 code 被两维度声明时归属冲突 → None，warning 与原因可诊断。"""
    dimensions = load_dimensions(db_session)
    duplicated = [
        dataclasses.replace(
            dimension,
            config=dataclasses.replace(
                dimension.config,
                data_sources=(
                    *dimension.config.data_sources,
                    DimensionDataSource("nmc-weather", "重复声明", "connected"),
                ),
            ),
        )
        if dimension.key == "geopolitical"
        else dimension
        for dimension in dimensions
    ]

    with caplog.at_level("WARNING", logger="app.risks.engine.processing"):
        resolved = resolve_dimension(duplicated, "weather", source_code="nmc-weather")

    assert resolved is None
    assert any("nmc-weather" in record.message for record in caplog.records)

    from app.risks.engine.processing import resolve_dimension_reason

    reason = resolve_dimension_reason(duplicated, "weather", "nmc-weather")
    assert "nmc-weather" in reason
    assert "重复声明" in reason


# ── 真实 process_event 路由 ─────────────────────────────────────────


def test_process_event_routes_policy_signal_by_source(db_session: Session) -> None:
    """policy DB 启用后，政策信源经真实 process_event 得到 policy 维度评分。"""
    _enable_policy(db_session)
    source = _source(db_session, "customs-announcement")
    _add_supplier(db_session, "SUP-POLICY-1", "政策测试供应商")
    signal = _active_signal(db_session, source, "policy-routing-1")
    analysis = _analysis(db_session, signal, _policy_result())

    outcome = process_event(db_session, signal, analysis, now_utc=NOW)

    assert outcome.alert_ids
    alerts = list(db_session.scalars(select(RiskAlert)))
    assert len(alerts) == 1
    assert alerts[0].score_detail["dimension"] == "policy"


def test_process_event_policy_disabled_produces_no_alert(
    db_session: Session,
) -> None:
    """policy 未启用时政策信源不产生提醒，且复核原因指明归属维度已禁用。"""
    source = _source(db_session, "customs-announcement")
    _add_supplier(db_session, "SUP-POLICY-2", "政策测试供应商")
    signal = _active_signal(db_session, source, "policy-routing-2")
    analysis = _analysis(db_session, signal, _policy_result())

    outcome = process_event(db_session, signal, analysis, now_utc=NOW)

    assert outcome.alert_ids == []
    assert analysis.needs_review is True
    assert analysis.review_reason is not None
    assert "customs-announcement" in analysis.review_reason
    assert "policy" in analysis.review_reason


# ── 维度 API 声明与 inputs 覆盖 ─────────────────────────────────────


def test_dimensions_api_declares_real_source_owners(client: TestClient) -> None:
    response = client.get("/api/v1/rule-engine/dimensions")
    assert response.status_code == 200
    by_key = {item["key"]: item for item in response.json()}

    for dimension_key, codes in EXPECTED_OWNERS.items():
        declared = {s["code"] for s in by_key[dimension_key]["data_sources"]}
        assert declared == set(codes), dimension_key


def test_policy_inputs_endpoint_reports_declared_sources(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/rule-engine/dimensions/policy/inputs")
    assert response.status_code == 200
    body = response.json()
    assert body["declared_total"] == len(POLICY_CODES)
    assert body["observed"] == []
