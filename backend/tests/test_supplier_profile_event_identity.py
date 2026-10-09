"""Todo 14：周期画像信号接入事件链（稳定事件身份 + 服务端注入主体）。

覆盖计划验收（Todo 14 行 205-211）：
- 同一 supplier_code 连续两周报告信号归并到同一事件 id，事件字段与 facts 用
  当前分析刷新（不留首周快照）；
- 两个均无 registry_no 的不同 supplier_code 不归并（各自独立事件）；
- AI 未抽到 organizations 时服务端注入仍命中主体（legal_name 精确匹配）；
- 画像报告分析为 compliance/sanctions 且强主体匹配 → 经强制规则得 P1；
  普通 judicial_case 走常规评分（见 test_profile_event_targeting.py 的同级回归）；
- 首周 corporate、次周 compliance/sanctions 时同一 event id 且次周强制 P1；
- 非画像信号走原身份逻辑（dedup 公式不变）；
- 缺失/空白 supplier_code 的报告不允许构造 override 且不得错误合并（明确失败）；
- identity_override 不含周次/主体字段，跨周恒定；空白 override 拒绝；
- 并发 find_or_create 冲突路径最终只有一行且返回同一事件。

执行环境：Compose 隔离测试栈，真实 PostgreSQL；报告经 store_tyc_report_signal
落库，AI 分析记录直接构造（不调用外部 Provider）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agent.tyc_report import DimensionFinding, DimensionStatus, RiskLevel, TycRiskReport
from app.agent.tyc_report_storage import store_tyc_report_signal
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.database import SessionLocal
from app.risks.engine.event_identity import (
    event_dedup_key,
    find_or_create_event,
    persist_event_facts,
)
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    SupplierEventMatch,
)
from app.risks.schemas import RiskProcessResult
from app.risks.service import process_analysis
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier
from app.suppliers.schemas import normalize_alias

_WEEK_1 = datetime(2026, 5, 6, 9, 30, tzinfo=UTC)
_WEEK_2 = _WEEK_1 + timedelta(days=7)
_NOW_1 = _WEEK_1 + timedelta(hours=1)
_NOW_2 = _WEEK_2 + timedelta(hours=1)

_PROFILE_POLICY = {
    "mode": "until_superseded",
    "fixed_days": 30,
}


# ── 辅助：源、供应商、报告、分析记录与处理 ──────────────────────────


def _prepare_source(session: Session) -> DataSource:
    """把天眼查源设为测试确定态（周度 until_superseded、credibility=50）。"""
    session.expire_all()
    source = session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None, "迁移应已注册 tianyancha 信息源"
    source.validity_policy = dict(_PROFILE_POLICY)
    source.credibility = 50
    session.flush()
    return source


def _supplier(
    session: Session,
    code: str,
    legal_name: str,
    *,
    registry_no: str | None = None,
) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=legal_name,
        country_code="CN",
        registry_no=registry_no,
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def _report(
    code: str,
    company_name: str,
    *,
    period: str,
    generated_at: datetime,
    credit_code: str | None = None,
) -> TycRiskReport:
    return TycRiskReport(
        company_name=company_name,
        supplier_code=code,
        credit_code=credit_code,
        reg_status="存续",
        generated_at=generated_at,
        period_key=f"tyc:{code}:{period}",
        dimensions=[
            DimensionFinding(
                key="get_risk_overview",
                name="风险总览",
                status=DimensionStatus.SUCCESS,
                risk_level=RiskLevel.ALERT,
                hit=True,
                summary="存在被执行记录",
                evidence_refs=["被执行 1 条"],
                raw_ref="dimensions.get_risk_overview.raw",
            )
        ],
    )


def _store(session: Session, supplier: Supplier, report: TycRiskReport) -> RawSignal:
    write = store_tyc_report_signal(session, supplier=supplier, report=report)
    assert write.outcome == "created"
    signal = session.scalar(
        select(RawSignal).where(RawSignal.external_id == write.external_id)
    )
    assert signal is not None
    return signal


def _result(
    *,
    event_type: str = "corporate",
    event_subtype: str | None = "corporate_distress",
    summary: str = "画像分析摘要",
    severity: str = "high",
    confidence: float = 0.9,
    organizations: list[dict[str, object]] | None = None,
    locations: list[dict[str, object]] | None = None,
) -> SignalAnalysisResult:
    """模拟 LLM 分析结果：默认不带 organizations，验证服务端注入。"""
    return SignalAnalysisResult(
        event_type=event_type,
        event_subtype=event_subtype,
        suggested_severity=severity,
        organizations=organizations or [],
        locations=locations or [],
        affected_activities=["operations"],
        affected_products=[],
        summary_zh=summary,
        evidence_sentences=["报告中存在风险命中。"],
        confidence=confidence,
    )


def _process(
    session: Session, signal: RawSignal, result: SignalAnalysisResult, *, now: datetime
) -> RiskProcessResult:
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
    return process_analysis(session, signal, record, now_utc=now)


def _events(session: Session) -> list[RiskEvent]:
    return list(session.scalars(select(RiskEvent).order_by(RiskEvent.id)))


def _current_alert(session: Session) -> RiskAlert:
    alert = session.scalar(
        select(RiskAlert)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .where(RiskAlert.status == "current")
    )
    assert alert is not None
    return alert


def _entity_names(session: Session, event_id: int) -> list[str]:
    return [
        row.name
        for row in session.scalars(
            select(EventEntity)
            .where(EventEntity.event_id == event_id)
            .order_by(EventEntity.id)
        )
    ]


def _location_names(session: Session, event_id: int) -> list[str]:
    return [
        row.name
        for row in session.scalars(
            select(EventLocation)
            .where(EventLocation.event_id == event_id)
            .order_by(EventLocation.id)
        )
    ]


def _unit_event(session: Session) -> RiskEvent:
    event = RiskEvent(
        dedup_key="unit-side-table-event",
        event_type="corporate",
        severity="high",
        summary="单元事件",
        confidence=0.9,
        facts={},
    )
    session.add(event)
    session.flush()
    return event


# ── 验收：跨周归并 / 无注册号隔离 / 服务端注入 / 强制 P1 ────────────


def test_same_supplier_two_weeks_share_event_id(db_session: Session) -> None:
    """同一 supplier_code 连续两周报告 → 同一事件 id，字段与 facts 刷新到次周。"""
    _prepare_source(db_session)
    supplier = _supplier(db_session, "SUP-WEEK-1", "跨周画像有限公司")
    first = _store(
        db_session,
        supplier,
        _report(
            "SUP-WEEK-1",
            "跨周画像有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
            credit_code="CC-WEEK-1",
        ),
    )
    result_1 = _process(
        db_session,
        first,
        _result(summary="首周摘要：经营异常", event_type="corporate"),
        now=_NOW_1,
    )

    second = _store(
        db_session,
        supplier,
        _report(
            "SUP-WEEK-1",
            "跨周画像有限公司",
            period="2026-W20",
            generated_at=_WEEK_2,
            credit_code="CC-WEEK-1",
        ),
    )
    result_2 = _process(
        db_session,
        second,
        _result(summary="次周摘要：被执行", event_type="corporate"),
        now=_NOW_2,
    )

    assert result_2.event_id == result_1.event_id
    assert result_2.event_created is False
    events = _events(db_session)
    assert len(events) == 1
    event = events[0]
    assert event.summary == "次周摘要：被执行"
    assert event.facts["summary_zh"] == "次周摘要：被执行"
    assert event.facts["event_type"] == "corporate"
    assert event.facts["organizations"][0]["name"] == "跨周画像有限公司"
    alert = _current_alert(db_session)
    assert alert.level == "P2"


def test_two_suppliers_without_registry_no_do_not_merge(db_session: Session) -> None:
    """两个均无 registry_no 的不同 supplier_code 各自独立事件，不归并。"""
    _prepare_source(db_session)
    supplier_a = _supplier(db_session, "SUP-NO-REG-A", "无注册号甲有限公司")
    supplier_b = _supplier(db_session, "SUP-NO-REG-B", "无注册号乙有限公司")
    signal_a = _store(
        db_session,
        supplier_a,
        _report(
            "SUP-NO-REG-A",
            "无注册号甲有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )
    signal_b = _store(
        db_session,
        supplier_b,
        _report(
            "SUP-NO-REG-B",
            "无注册号乙有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    result_a = _process(db_session, signal_a, _result(summary="甲画像"), now=_NOW_1)
    result_b = _process(db_session, signal_b, _result(summary="乙画像"), now=_NOW_1)

    assert result_a.event_id != result_b.event_id
    events = _events(db_session)
    assert len(events) == 2
    assert {event.id for event in events} == {result_a.event_id, result_b.event_id}
    matches = list(db_session.scalars(select(SupplierEventMatch)))
    assert {match.supplier_id for match in matches} == {supplier_a.id, supplier_b.id}
    assert len({match.event_id for match in matches}) == 2


def test_injected_subject_matches_without_ai_organizations(db_session: Session) -> None:
    """AI 未抽到 organizations：服务端注入报告主体后仍命中法人全称。"""
    _prepare_source(db_session)
    supplier = _supplier(db_session, "SUP-INJECT-1", "注入主体测试有限公司")
    signal = _store(
        db_session,
        supplier,
        _report(
            "SUP-INJECT-1",
            "注入主体测试有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    outcome = _process(db_session, signal, _result(), now=_NOW_1)

    assert outcome.alert_ids
    match = db_session.scalar(select(SupplierEventMatch))
    assert match is not None
    assert "legal_name" in match.match_type.split("+")
    assert any("法人全称" in reason for reason in match.reasons)
    event = _events(db_session)[0]
    assert event.facts["organizations"][0]["name"] == "注入主体测试有限公司"


def test_judicial_profile_strong_match_uses_regular_scoring(
    db_session: Session,
) -> None:
    """画像分析为 judicial/judicial_case：不再被默认制裁强制规则提升为 P1。"""
    _prepare_source(db_session)
    supplier = _supplier(db_session, "SUP-JUD-1", "司法画像有限公司")
    signal = _store(
        db_session,
        supplier,
        _report("SUP-JUD-1", "司法画像有限公司", period="2026-W19", generated_at=_WEEK_1),
    )

    outcome = _process(
        db_session,
        signal,
        _result(
            event_type="judicial",
            event_subtype="judicial_case",
            severity="medium",
            summary="司法画像摘要",
        ),
        now=_NOW_1,
    )

    assert outcome.alert_ids
    alert = _current_alert(db_session)
    assert "forced_rule" not in alert.score_detail
    assert alert.level != "P1"
    assert alert.score_detail["final_level"] == alert.level


def test_compliance_sanctions_profile_strong_match_forces_p1(
    db_session: Session,
) -> None:
    """画像分析为 compliance/sanctions 且强主体匹配 → 强制规则仍得 P1。"""
    _prepare_source(db_session)
    supplier = _supplier(db_session, "SUP-SANC-ID-1", "制裁画像主体有限公司")
    signal = _store(
        db_session,
        supplier,
        _report(
            "SUP-SANC-ID-1",
            "制裁画像主体有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    outcome = _process(
        db_session,
        signal,
        _result(
            event_type="compliance",
            event_subtype="sanctions",
            severity="medium",
            summary="制裁画像摘要",
        ),
        now=_NOW_1,
    )

    assert outcome.alert_ids
    alert = _current_alert(db_session)
    assert alert.level == "P1"
    assert alert.score == 100
    forced = alert.score_detail.get("forced_rule")
    assert isinstance(forced, dict)
    assert forced["name"] == "sanctions_entity_hit"
    assert alert.score_detail["final_level"] == "P1"


def test_corporate_then_compliance_sanctions_same_event_and_fields_refreshed(
    db_session: Session,
) -> None:
    """首周 corporate、次周 compliance/sanctions：同一 event id，次周字段刷新并强制 P1。"""
    _prepare_source(db_session)
    supplier = _supplier(db_session, "SUP-FLIP-1", "类型翻转有限公司")
    first = _store(
        db_session,
        supplier,
        _report("SUP-FLIP-1", "类型翻转有限公司", period="2026-W19", generated_at=_WEEK_1),
    )
    result_1 = _process(
        db_session,
        first,
        _result(
            event_type="corporate",
            event_subtype="corporate_distress",
            summary="首周企业风险",
        ),
        now=_NOW_1,
    )
    event = db_session.get(RiskEvent, result_1.event_id)
    assert event is not None
    assert event.event_type == "corporate"
    assert _current_alert(db_session).level == "P2"

    second = _store(
        db_session,
        supplier,
        _report("SUP-FLIP-1", "类型翻转有限公司", period="2026-W20", generated_at=_WEEK_2),
    )
    result_2 = _process(
        db_session,
        second,
        _result(
            event_type="compliance",
            event_subtype="sanctions",
            summary="次周制裁风险",
        ),
        now=_NOW_2,
    )

    assert result_2.event_id == result_1.event_id
    db_session.refresh(event)
    assert event.event_type == "compliance"
    assert event.event_subtype == "sanctions"
    assert event.summary == "次周制裁风险"
    assert event.facts["event_type"] == "compliance"
    assert event.facts["summary_zh"] == "次周制裁风险"
    assert event.facts["organizations"][0]["name"] == "类型翻转有限公司"
    assert len(_events(db_session)) == 1
    alert = _current_alert(db_session)
    assert alert.level == "P1"
    assert alert.score == 100
    assert alert.score_detail["final_level"] == "P1"


def test_weekly_subject_change_replaces_event_side_tables(db_session: Session) -> None:
    """同一 supplier_code 跨周刷新：侧表随当前周精确替换，历史 Match 保留、旧 alert 不 current。

    目标供应商由报告 supplier_code 唯一决定；名称改投其他供应商的场景见
    test_profile_report_does_not_reroute_to_name_matched_supplier（非目标零命中）。
    """
    _prepare_source(db_session)
    carrier = _supplier(db_session, "SUP-SIDE-1", "甲主体有限公司")
    signal_1 = _store(
        db_session,
        carrier,
        _report(
            "SUP-SIDE-1",
            "甲主体有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
            credit_code="CC-SIDE-A",
        ),
    )
    result_1 = _process(
        db_session,
        signal_1,
        _result(
            summary="首周甲",
            locations=[{"name": "上海浦东", "country_code": "CN", "city": "上海市"}],
        ),
        now=_NOW_1,
    )
    assert _entity_names(db_session, result_1.event_id) == ["甲主体有限公司"]
    assert _location_names(db_session, result_1.event_id) == ["上海浦东"]

    signal_2 = _store(
        db_session,
        carrier,
        _report(
            "SUP-SIDE-1",
            "甲主体有限公司",
            period="2026-W20",
            generated_at=_WEEK_2,
            credit_code="CC-SIDE-B",
        ),
    )
    result_2 = _process(
        db_session,
        signal_2,
        _result(
            summary="次周甲",
            locations=[{"name": "杭州西湖", "country_code": "CN", "city": "杭州市"}],
        ),
        now=_NOW_2,
    )

    assert result_2.event_id == result_1.event_id
    event = db_session.get(RiskEvent, result_1.event_id)
    assert event is not None
    # 侧表精确反映当前周：仅当前主体/杭州西湖，旧地点不残留。
    entities = list(
        db_session.scalars(
            select(EventEntity)
            .where(EventEntity.event_id == event.id)
            .order_by(EventEntity.id)
        )
    )
    assert [entity.name for entity in entities] == ["甲主体有限公司"]
    assert entities[0].registry_no == "CC-SIDE-B"
    locations = list(
        db_session.scalars(
            select(EventLocation)
            .where(EventLocation.event_id == event.id)
            .order_by(EventLocation.id)
        )
    )
    assert [location.name for location in locations] == ["杭州西湖"]
    assert locations[0].city == "杭州市"
    # facts 与侧表一致（同一当前 result 快照）。
    assert [item["name"] for item in event.facts["organizations"]] == ["甲主体有限公司"]
    assert [item["name"] for item in event.facts["locations"]] == ["杭州西湖"]
    # 非目标供应商从未被画像事件命中：target 由 supplier_code 唯一确定。
    matches = list(db_session.scalars(select(SupplierEventMatch)))
    assert {match.supplier_id for match in matches} == {carrier.id}
    current = _current_alert(db_session)
    current_match = db_session.get(SupplierEventMatch, current.match_id)
    assert current_match is not None
    assert current_match.supplier_id == carrier.id
    assert current.status == "current"


def test_profile_report_does_not_reroute_to_name_matched_supplier(
    db_session: Session,
) -> None:
    """报告 supplier_code 属甲但 company_name 是乙的法人全称：只按甲定位，乙零命中。"""
    _prepare_source(db_session)
    carrier = _supplier(db_session, "SUP-REROUTE-1", "甲画像主体有限公司")
    supplier_b = _supplier(db_session, "SUP-REROUTE-B", "乙画像主体有限公司")
    signal = _store(
        db_session,
        carrier,
        _report(
            "SUP-REROUTE-1",
            "乙画像主体有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    outcome = _process(db_session, signal, _result(summary="名称与编码不一致"), now=_NOW_1)

    # company_name 与目标 legal_name 不一致 → 不产生任何 match（fail closed）。
    assert outcome.alert_ids == []
    assert _entity_names(db_session, outcome.event_id) == ["乙画像主体有限公司"]
    matches = list(db_session.scalars(select(SupplierEventMatch)))
    assert {match.supplier_id for match in matches} == set()
    assert supplier_b.id != carrier.id


def test_weekly_refresh_does_not_cross_supplier_boundaries(db_session: Session) -> None:
    """A 供应商跨周刷新只作用于自身事件：B 的事件/提醒不被污染（主体不串线）。"""
    _prepare_source(db_session)
    supplier_a = _supplier(db_session, "SUP-X-A", "甲画像有限公司")
    supplier_b = _supplier(db_session, "SUP-X-B", "乙画像有限公司")
    signal_a1 = _store(
        db_session,
        supplier_a,
        _report("SUP-X-A", "甲画像有限公司", period="2026-W19", generated_at=_WEEK_1),
    )
    result_a1 = _process(db_session, signal_a1, _result(summary="A 首周"), now=_NOW_1)
    signal_b1 = _store(
        db_session,
        supplier_b,
        _report("SUP-X-B", "乙画像有限公司", period="2026-W19", generated_at=_WEEK_1),
    )
    result_b1 = _process(db_session, signal_b1, _result(summary="B 首周"), now=_NOW_1)

    signal_a2 = _store(
        db_session,
        supplier_a,
        _report("SUP-X-A", "甲画像有限公司", period="2026-W20", generated_at=_WEEK_2),
    )
    result_a2 = _process(
        db_session,
        signal_a2,
        _result(event_type="judicial", event_subtype="judicial_case", summary="A 次周"),
        now=_NOW_2,
    )

    assert result_a2.event_id == result_a1.event_id
    assert result_b1.event_id != result_a1.event_id
    event_a = db_session.get(RiskEvent, result_a1.event_id)
    event_b = db_session.get(RiskEvent, result_b1.event_id)
    assert event_a is not None and event_b is not None
    assert event_a.summary == "A 次周"
    # B 的事件保持自己的首周快照，没有被 A 的刷新串改。
    assert event_b.summary == "B 首周"
    assert event_b.facts["organizations"][0]["name"] == "乙画像有限公司"
    matches = list(db_session.scalars(select(SupplierEventMatch)))
    match_by_supplier = {match.supplier_id: match for match in matches}
    assert set(match_by_supplier) == {supplier_a.id, supplier_b.id}
    assert match_by_supplier[supplier_a.id].event_id == result_a1.event_id
    assert match_by_supplier[supplier_b.id].event_id == result_b1.event_id


# ── 非画像路径不变 / 身份键语义单元测试 ─────────────────────────────


def _legacy_dedup_key(result: SignalAnalysisResult) -> str:
    """改动前的字面实现：用于锁定非画像事件身份不变。"""
    organizations = sorted(
        (normalize_alias(item.name), item.registry_no or "")
        for item in result.organizations
    )
    locations: list[tuple[object, ...]] = []
    for item in result.locations:
        location_identity: tuple[object, ...] = (
            normalize_alias(item.name),
            item.country_code or "",
            item.region or "",
            item.city or "",
            round(item.latitude, 6) if item.latitude is not None else None,
            round(item.longitude, 6) if item.longitude is not None else None,
            item.radius_km,
        )
        if item.district:
            location_identity += (normalize_alias(item.district),)
        locations.append(location_identity)
    locations.sort()
    event_identity: dict[str, object] = {
        "type": result.event_type,
        "subtype": result.event_subtype,
        "organizations": organizations,
        "locations": locations,
        "start_date": result.start_at.date().isoformat() if result.start_at else None,
    }
    if not organizations and not locations and result.start_at is None:
        event_identity["summary"] = normalize_alias(result.summary_zh)
    encoded = json.dumps(event_identity, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_regular_signal_dedup_unchanged() -> None:
    """非画像信号（无 override）的身份键与改动前公式逐字节一致。"""
    result = SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="high",
        organizations=[{"name": "某公司", "aliases": [], "registry_no": "9131"}],
        locations=[{"name": "上海市", "country_code": "CN", "city": "上海市"}],
        affected_activities=["production"],
        affected_products=[],
        start_at="2026-08-11T08:00:00+08:00",
        summary_zh="台风摘要",
        evidence_sentences=["证据"],
        confidence=0.9,
    )
    assert event_dedup_key(result) == _legacy_dedup_key(result)
    assert event_dedup_key(result) == event_dedup_key(result, None)


def test_identity_override_key_is_stable_and_week_agnostic() -> None:
    """override 键只取决于身份字符串：跨周/主体字段/类型变化均不影响。"""
    override = "supplier_profile:SUP-STABLE-1"
    week_1 = SignalAnalysisResult(
        event_type="corporate",
        event_subtype="corporate_distress",
        suggested_severity="high",
        organizations=[{"name": "第一周公司名", "aliases": [], "registry_no": "R1"}],
        locations=[],
        affected_activities=[],
        affected_products=[],
        start_at="2026-05-06T09:30:00+00:00",
        summary_zh="第一周摘要",
        evidence_sentences=["证据"],
        confidence=0.9,
    )
    week_2 = SignalAnalysisResult(
        event_type="judicial",
        event_subtype="judicial_case",
        suggested_severity="low",
        organizations=[],
        locations=[{"name": "杭州市", "country_code": "CN"}],
        affected_activities=[],
        affected_products=[],
        start_at="2026-05-13T09:30:00+00:00",
        summary_zh="第二周摘要",
        evidence_sentences=["证据"],
        confidence=0.5,
    )
    assert event_dedup_key(week_1, override) == event_dedup_key(week_2, override)
    assert event_dedup_key(week_1, override) != event_dedup_key(
        week_2, "supplier_profile:SUP-STABLE-2"
    )
    assert event_dedup_key(week_1, override) != event_dedup_key(week_1)


def test_blank_identity_override_is_rejected() -> None:
    """空白 override 不允许构造身份：安全失败，绝不回退普通逻辑。"""
    result = SignalAnalysisResult(
        event_type="weather",
        event_subtype="weather_alert",
        suggested_severity="high",
        organizations=[],
        locations=[],
        affected_activities=[],
        affected_products=[],
        summary_zh="摘要",
        evidence_sentences=["证据"],
        confidence=0.9,
    )
    with pytest.raises(ValueError):
        event_dedup_key(result, "   ")


# ── 侧表 replace 语义：画像精确替换 / 普通 add-only / 同名字段刷新 ──


def test_replace_persist_clears_side_tables_when_current_empty(
    db_session: Session,
) -> None:
    """replace=True 且当前集合为空：清空该事件全部侧表行，不残留旧周。"""
    event = _unit_event(db_session)
    persist_event_facts(
        db_session,
        event,
        _result(
            organizations=[{"name": "旧主体有限公司", "aliases": []}],
            locations=[{"name": "旧地点", "country_code": "CN"}],
        ),
        replace=True,
    )
    assert _entity_names(db_session, event.id) == ["旧主体有限公司"]
    assert _location_names(db_session, event.id) == ["旧地点"]

    persist_event_facts(db_session, event, _result(), replace=True)

    assert _entity_names(db_session, event.id) == []
    assert _location_names(db_session, event.id) == []


def test_regular_persist_remains_add_only(db_session: Session) -> None:
    """普通事件（默认 replace=False）两次 persist 仍追加，既有语义不变。"""
    event = _unit_event(db_session)
    persist_event_facts(
        db_session,
        event,
        _result(
            organizations=[{"name": "首次主体", "aliases": []}],
            locations=[{"name": "首次地点", "country_code": "CN"}],
        ),
    )
    persist_event_facts(
        db_session,
        event,
        _result(
            organizations=[{"name": "二次主体", "aliases": []}],
            locations=[{"name": "二次地点", "country_code": "CN"}],
        ),
    )
    assert _entity_names(db_session, event.id) == ["首次主体", "二次主体"]
    assert _location_names(db_session, event.id) == ["首次地点", "二次地点"]


def test_replace_persist_refreshes_same_name_fields(db_session: Session) -> None:
    """replace=True 同名保留时刷新 registry_no 与地点字段，而非仅跳过。"""
    event = _unit_event(db_session)
    persist_event_facts(
        db_session,
        event,
        _result(
            organizations=[
                {"name": "同名主体有限公司", "aliases": [], "registry_no": None}
            ],
            locations=[{"name": "同名地点", "country_code": "CN", "city": "上海市"}],
        ),
        replace=True,
    )
    persist_event_facts(
        db_session,
        event,
        _result(
            organizations=[
                {
                    "name": "同名主体有限公司",
                    "aliases": [],
                    "registry_no": "9131REFRESH",
                }
            ],
            locations=[{"name": "同名地点", "country_code": "CN", "city": "杭州市"}],
        ),
        replace=True,
    )
    entities = list(
        db_session.scalars(select(EventEntity).where(EventEntity.event_id == event.id))
    )
    assert len(entities) == 1
    assert entities[0].registry_no == "9131REFRESH"
    locations = list(
        db_session.scalars(
            select(EventLocation).where(EventLocation.event_id == event.id)
        )
    )
    assert len(locations) == 1
    assert locations[0].city == "杭州市"


# ── 缺失/空白 supplier_code：明确失败且不合并 ────────────────────────


def _fake_profile_signal(
    db_session: Session, raw_data: dict[str, object], suffix: str
) -> RawSignal:
    source = _prepare_source(db_session)
    signal = RawSignal(
        source_id=source.id,
        external_id=f"fake-profile-{suffix}",
        title="伪造画像信号",
        content="伪造画像信号",
        collected_at=_WEEK_1,
        fingerprint=f"fake-profile-{suffix}",
        raw_data=raw_data,
        validity_profile="adverse_registry",
        validity_state="active",
        valid_from=_WEEK_1,
        valid_until=None,
        validity_mode="until_revoked",
        lifecycle_action="assert",
        validity_policy_version="test-policy",
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "collected_at",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.flush()
    return signal


@pytest.mark.parametrize(
    ("raw_data", "suffix"),
    [
        (
            {"report_kind": "supplier_profile", "company_name": "缺代码公司"},
            "missing-code",
        ),
        (
            {
                "report_kind": "supplier_profile",
                "company_name": "空代码公司",
                "supplier_code": "   ",
                "credit_code": None,
                "reg_status": None,
                "generated_at": "2026-05-06T09:30:00+00:00",
                "period_key": "tyc:blank:2026-W19",
                "dimensions": [],
            },
            "blank-code",
        ),
    ],
)
def test_blank_or_missing_supplier_code_fails_without_merge(
    db_session: Session, raw_data: dict[str, object], suffix: str
) -> None:
    """失败安全：明确失败且不创建事件，不得用普通逻辑错误合并。"""
    signal = _fake_profile_signal(db_session, raw_data, suffix)
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider="static-test",
        model="static-v1",
        prompt_version="signal-analysis-v3",
        status="succeeded",
        started_at=_WEEK_1,
        finished_at=_WEEK_1,
        result=_result().model_dump(mode="json"),
    )
    db_session.add(record)
    db_session.flush()

    with pytest.raises(ValueError, match="supplier_profile"):
        process_analysis(db_session, signal, record, now_utc=_NOW_1)

    assert db_session.scalar(select(func.count()).select_from(RiskEvent)) == 0
    assert db_session.scalar(select(func.count()).select_from(RiskAlert)) == 0


# ── 并发冲突路径 ────────────────────────────────────────────────────


def test_concurrent_find_or_create_conflict_stays_single_event() -> None:
    """并发创建同一 override 事件：冲突路径回滚保存点重查，最终单行同一 id。"""
    override = "supplier_profile:SUP-CONC-1"
    result = _result(summary="并发画像摘要")
    dedup_key = event_dedup_key(result, override)

    inserted = threading.Event()
    release = threading.Event()
    outcome: dict[str, tuple[int, bool]] = {}

    def first() -> None:
        with SessionLocal() as session:
            event, created = find_or_create_event(
                session, result, identity_override=override
            )
            outcome["first"] = (event.id, created)
            inserted.set()
            assert release.wait(timeout=15)
            session.commit()

    def second() -> None:
        assert inserted.wait(timeout=15)
        with SessionLocal() as session:
            event, created = find_or_create_event(
                session, result, identity_override=override
            )
            outcome["second"] = (event.id, created)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        assert inserted.wait(timeout=15)
        # 留给第二个线程进入 INSERT 阻塞的窗口后，再允许第一个提交。
        time.sleep(0.5)
        release.set()
        first_future.result(timeout=30)
        second_future.result(timeout=30)

    assert outcome["first"][1] is True
    assert outcome["second"] == (outcome["first"][0], False)

    with SessionLocal() as cleanup:
        count = cleanup.scalar(
            select(func.count())
            .select_from(RiskEvent)
            .where(RiskEvent.dedup_key == dedup_key)
        )
        assert count == 1
        cleanup.execute(delete(RiskEvent).where(RiskEvent.dedup_key == dedup_key))
        cleanup.commit()
