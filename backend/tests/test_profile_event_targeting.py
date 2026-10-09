"""画像事件主体定位与风险规则修复回归（授权修复项）。

覆盖用户已授权的三处规则缺陷：

1. supplier_profile 不扩散：画像报告的受影响产品/地点不得匹配到非目标供应商；
   目标由可信 ``raw_data.supplier_code`` 在启用清单中唯一定位，无法定位即 fail closed。
2. 泛化产品子串不误匹配：事件"电梯"不得靠双向子串命中供应商"电梯配件"；
   具体长词（高精度轴承）仍必须命中；空串永不命中。
3. 普通 judicial_case 不自动 P1：默认 sanctions_entity_hit 只对明确制裁子类型生效，
   真正制裁/出口管制既有正例保留。

另锁定两项相邻契约：
- 重放时同事件非目标 current 提醒被失效，旧 match/原评分保留且写入机器可读审计原因；
- 有效 rule_version 与旧提醒历史（expired 行不被删除）在修复后仍然成立。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tyc_report import DimensionFinding, DimensionStatus, RiskLevel, TycRiskReport
from app.agent.tyc_report_storage import store_tyc_report_signal
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.engine.matching import match_products
from app.risks.models import RiskAlert, SupplierEventMatch
from app.risks.schemas import RiskProcessResult
from app.risks.scoring import ForcedRule, ScoringSettings
from app.risks.service import process_analysis
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier, SupplierProduct

_WEEK_1 = datetime(2026, 5, 6, 9, 30, tzinfo=UTC)
_WEEK_2 = _WEEK_1 + timedelta(days=7)
_NOW_1 = _WEEK_1 + timedelta(hours=1)
_NOW_2 = _WEEK_2 + timedelta(hours=1)

_PROFILE_POLICY = {"mode": "until_superseded", "fixed_days": 30}


# ── 辅助：源、供应商、报告、分析记录与处理 ──────────────────────────


def _prepare_source(session: Session) -> DataSource:
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
    enabled: bool = True,
    products: list[tuple[str, list[str]]] | None = None,
) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=legal_name,
        country_code="CN",
        enabled=enabled,
    )
    session.add(supplier)
    session.flush()
    for name, keywords in products or []:
        session.add(
            SupplierProduct(supplier_id=supplier.id, name=name, keywords=keywords)
        )
    session.flush()
    session.refresh(supplier)
    return supplier


def _report(
    code: str, company_name: str, *, period: str, generated_at: datetime
) -> TycRiskReport:
    return TycRiskReport(
        company_name=company_name,
        supplier_code=code,
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
    severity: str = "medium",
    affected_products: list[str] | None = None,
    confidence: float = 0.7,
) -> SignalAnalysisResult:
    """默认不带 organizations：验证服务端按 supplier_code 注入主体。"""
    return SignalAnalysisResult(
        event_type=event_type,
        event_subtype=event_subtype,
        suggested_severity=severity,
        organizations=[],
        locations=[],
        affected_activities=["operations"],
        affected_products=affected_products or [],
        summary_zh="画像分析摘要",
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


def _matched_supplier_ids(session: Session, event_id: int) -> set[int]:
    return set(
        session.scalars(
            select(SupplierEventMatch.supplier_id).where(
                SupplierEventMatch.event_id == event_id
            )
        )
    )


def _alerts_by_supplier(session: Session, event_id: int) -> dict[int, list[RiskAlert]]:
    rows = list(
        session.scalars(
            select(RiskAlert)
            .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
            .where(SupplierEventMatch.event_id == event_id)
            .order_by(RiskAlert.id)
        )
    )
    grouped: dict[int, list[RiskAlert]] = {}
    for alert in rows:
        match = session.get(SupplierEventMatch, alert.match_id)
        assert match is not None
        grouped.setdefault(match.supplier_id, []).append(alert)
    return grouped


# ── 1. supplier_profile 不扩散（目标失效/停用/不存在 fail closed） ──


def test_profile_product_hit_does_not_reach_other_suppliers(
    db_session: Session,
) -> None:
    """Given 目标与另一家产品相同的供应商，When 画像报告处理，Then 只有目标被提醒。"""
    _prepare_source(db_session)
    target = _supplier(db_session, "SUP-TARGET-1", "画像目标有限公司")
    other = _supplier(
        db_session,
        "SUP-OTHER-1",
        "画像无关有限公司",
        products=[("电梯配件", ["电梯配件"])],
    )
    signal = _store(
        db_session,
        target,
        _report("SUP-TARGET-1", "画像目标有限公司", period="2026-W19", generated_at=_WEEK_1),
    )

    outcome = _process(
        db_session,
        signal,
        _result(affected_products=["电梯配件"]),
        now=_NOW_1,
    )

    assert outcome.alert_ids
    assert _matched_supplier_ids(db_session, outcome.event_id) == {target.id}
    assert other.id not in _matched_supplier_ids(db_session, outcome.event_id)


def test_profile_target_disabled_fails_closed(db_session: Session) -> None:
    """Given 画像目标供应商已停用，When 处理画像报告，Then 明确失败且不产生提醒。"""
    _prepare_source(db_session)
    target = _supplier(db_session, "SUP-DISABLED-1", "停用画像有限公司", enabled=False)
    signal = _store(
        db_session,
        target,
        _report(
            "SUP-DISABLED-1",
            "停用画像有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    with pytest.raises(ValueError, match="supplier_profile"):
        _process(db_session, signal, _result(affected_products=["电梯"]), now=_NOW_1)

    assert db_session.scalar(select(RiskAlert)) is None


def test_profile_target_absent_fails_closed(db_session: Session) -> None:
    """Given 报告 supplier_code 在启用清单中不存在，When 处理，Then 明确失败无提醒。"""
    _prepare_source(db_session)
    carrier = _supplier(db_session, "SUP-CARRIER-1", "载体画像有限公司")
    signal = _store(
        db_session,
        carrier,
        _report(
            "SUP-GHOST-1",
            "幽灵画像有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    with pytest.raises(ValueError, match="supplier_profile"):
        _process(db_session, signal, _result(), now=_NOW_1)

    assert db_session.scalar(select(RiskAlert)) is None


# ── 2. 重放撤销误报：非目标 current 提醒失效但保留历史与审计 ──


def test_replay_expires_off_target_alerts_keeping_history(db_session: Session) -> None:
    """Given 同事件上仍有非目标 current 误报，When 画像重放，Then 失效并留审计原因。"""
    _prepare_source(db_session)
    target = _supplier(db_session, "SUP-REPLAY-1", "重放目标有限公司")
    other = _supplier(
        db_session, "SUP-REPLAY-OTHER", "重放无关有限公司", products=[("电梯配件", [])]
    )
    first = _store(
        db_session,
        target,
        _report("SUP-REPLAY-1", "重放目标有限公司", period="2026-W19", generated_at=_WEEK_1),
    )
    # 首周：服务端已按 supplier_code 定位目标，other 不会产生 match/alert。
    outcome_1 = _process(
        db_session, first, _result(affected_products=["电梯配件"]), now=_NOW_1
    )
    assert _matched_supplier_ids(db_session, outcome_1.event_id) == {target.id}

    # 构造历史误报：模拟修复前遗留的 other 侧 match + current 提醒（非 legacy）。
    stale_match = SupplierEventMatch(
        supplier_id=other.id,
        event_id=outcome_1.event_id,
        match_type="product",
        score=12,
        reasons=["受影响产品关键词匹配：电梯 → 电梯配件"],
        evidence=[{"object_type": "product", "supplier_id": other.id}],
    )
    db_session.add(stale_match)
    db_session.flush()
    stale_alert = RiskAlert(
        match_id=stale_match.id,
        level="P3",
        score=60,
        score_detail={
            "rule_version": "risk-score-v1-corporate-stale",
            "final_level": "P3",
        },
        status="current",
        expires_at=None,
        expiry_kind="unbounded",
    )
    db_session.add(stale_alert)
    db_session.flush()
    # 目标提醒同样保持 current，使本用例只断言非目标收敛这一件事。
    target_alert = _alerts_by_supplier(db_session, outcome_1.event_id)[target.id][0]
    assert target_alert.status == "current"

    # 重放同一周信号：事件身份不变，画像路径重新判定目标并收敛非目标提醒。
    outcome_2 = _process(
        db_session, first, _result(affected_products=["电梯配件"]), now=_NOW_1
    )

    assert outcome_2.event_id == outcome_1.event_id
    grouped = _alerts_by_supplier(db_session, outcome_2.event_id)
    # 历史误报行保留且已失效，带机器可读审计原因。
    assert [alert.status for alert in grouped[other.id]] == ["expired"]
    stale = grouped[other.id][0]
    assert stale.level == "P3"
    assert stale.score == 60
    audit = stale.score_detail.get("off_target_expiry")
    assert isinstance(audit, dict)
    assert audit["code"] == "supplier_profile_target_only"
    assert audit["target_supplier_id"] == target.id
    assert audit["off_target_supplier_id"] == other.id
    # 旧 match 仍在（历史证据闭包不被破坏），原评分明细保留。
    assert db_session.get(SupplierEventMatch, stale_match.id) is not None
    assert stale.score_detail["final_level"] == "P3"
    # 只有目标保持 current。
    assert [alert.status for alert in grouped[target.id]] == ["current"]


# ── 3. 产品匹配：短词精确命中、长词有效、双向子串不扩散、空串不命中 ──


def _product_matches(supplier_products: list[tuple[str, list[str]]], affected: list[str]) -> bool:
    supplier = Supplier(
        supplier_code="UNIT-1", legal_name="单元供应商", country_code="CN", enabled=True
    )
    for name, keywords in supplier_products:
        supplier.products.append(SupplierProduct(name=name, keywords=keywords))
    matches: dict[int, object] = {}
    match_products(
        None,  # type: ignore[arg-type]  # 产品柱不访问数据库
        _result(affected_products=affected),
        [supplier],
        {"product": 12},
        matches,  # type: ignore[arg-type]
    )
    return bool(matches)


def test_generic_product_does_not_match_specific_product_name() -> None:
    """Given 事件泛化产品“电梯”，When 供应商产品“电梯配件”，Then 不命中。"""
    assert _product_matches([("电梯配件", ["电梯配件"])], ["电梯"]) is False


def test_generic_product_does_not_match_specific_product_keyword() -> None:
    """Given 事件泛化产品“电梯”，When 供应关键词为更具体的“电梯部件”，Then 不命中。"""
    assert _product_matches([("整机", ["电梯部件"])], ["电梯"]) is False


def test_exact_short_product_still_matches() -> None:
    """Given 事件与产品同为短词“电梯”，When 完全相等，Then 精确命中。"""
    assert _product_matches([("电梯", [])], ["电梯"]) is True


def test_specific_long_product_still_matches() -> None:
    """Given 事件“高精度轴承”，When 关键词“轴承”，Then 单向包含仍然命中。"""
    assert _product_matches([("精密零部件", ["轴承", "精密加工"])], ["高精度轴承"]) is True


def test_supplier_longer_product_still_matches_longer_event() -> None:
    """Given 供应“电梯配件”对事件“电梯配件总成”，When 单向包含，Then 仍然命中。"""
    assert _product_matches([("电梯配件", [])], ["电梯配件总成"]) is True


@pytest.mark.parametrize("affected", [["   "], [""], ["", "  "]])
def test_blank_affected_product_never_matches(affected: list[str]) -> None:
    """Given 事件产品只有空白项，When 任意供应产品，Then 永不命中。"""
    assert _product_matches([("电梯", []), ("精密零部件", ["轴承"])], affected) is False


# ── 4. 普通 judicial_case 不自动 P1；真实制裁仍 P1 ──


def test_ordinary_judicial_case_is_not_forced_to_p1(db_session: Session) -> None:
    """Given 画像分析为 judicial/judicial_case，When 处理，Then 走常规评分而非强制 P1。"""
    _prepare_source(db_session)
    target = _supplier(db_session, "SUP-JUD-PLAIN", "普通司法画像有限公司")
    signal = _store(
        db_session,
        target,
        _report(
            "SUP-JUD-PLAIN",
            "普通司法画像有限公司",
            period="2026-W19",
            generated_at=_WEEK_1,
        ),
    )

    outcome = _process(
        db_session,
        signal,
        _result(
            event_type="judicial", event_subtype="judicial_case", severity="medium"
        ),
        now=_NOW_1,
    )

    assert outcome.alert_ids
    alert = _alerts_by_supplier(db_session, outcome.event_id)[target.id][0]
    assert "forced_rule" not in alert.score_detail
    assert alert.level != "P1"
    assert alert.score < 100
    assert alert.score_detail["rule_version"] != "risk-score-v1-corporate-stale"


def test_compliance_sanctions_subtype_still_forces_p1(db_session: Session) -> None:
    """Given 画像分析为 compliance/sanctions，When 处理，Then 保留既有强制 P1 正例。"""
    _prepare_source(db_session)
    target = _supplier(db_session, "SUP-SANC-1", "制裁画像有限公司")
    signal = _store(
        db_session,
        target,
        _report(
            "SUP-SANC-1", "制裁画像有限公司", period="2026-W19", generated_at=_WEEK_1
        ),
    )

    outcome = _process(
        db_session,
        signal,
        _result(event_type="compliance", event_subtype="sanctions", severity="medium"),
        now=_NOW_1,
    )

    assert outcome.alert_ids
    alert = _alerts_by_supplier(db_session, outcome.event_id)[target.id][0]
    assert alert.level == "P1"
    assert alert.score == 100
    forced = alert.score_detail.get("forced_rule")
    assert isinstance(forced, dict)
    assert forced["name"] == "sanctions_entity_hit"


# ── 5. 规则版本切割：旧规则版本的 P1 提醒不得被复用为 current ──


def test_rule_version_covers_forced_rules(db_session: Session) -> None:
    """Given 强制规则定义，When 合成维度评分，Then 规则版本随其内容变化。"""
    from app.risks.engine.dimensions.corporate import DIMENSION
    from app.risks.engine.registry import build_scoring

    current = build_scoring(DIMENSION, {}, None).rule_version
    rule = current and ScoringSettings().forced_rules[0]
    assert rule is not None

    def digest_with(rules: tuple[ForcedRule, ...]) -> str:
        return build_scoring(
            DIMENSION, {"forced_rules": [dataclasses.asdict(r) for r in rules]}, None
        ).rule_version

    # 去掉 event_subtypes 限定（即修复前的粗放定义）必须得到不同规则版本。
    relaxed = dataclasses.replace(rule, event_subtypes=())
    assert digest_with((relaxed,)) != current


def test_stale_p1_alert_is_cut_over_not_kept_current(db_session: Session) -> None:
    """Given 目标供应商仍有旧规则版本写入的 P1 提醒，When 重算，Then 旧行 expired、新行非 P1。"""
    _prepare_source(db_session)
    target = _supplier(db_session, "SUP-VER-1", "版本切割有限公司")
    signal = _store(
        db_session,
        target,
        _report("SUP-VER-1", "版本切割有限公司", period="2026-W19", generated_at=_WEEK_1),
    )
    result = _result(
        event_type="judicial", event_subtype="judicial_case", severity="medium"
    )
    outcome = _process(db_session, signal, result, now=_NOW_1)
    assert outcome.alert_ids
    match = db_session.scalar(
        select(SupplierEventMatch).where(
            SupplierEventMatch.event_id == outcome.event_id,
            SupplierEventMatch.supplier_id == target.id,
        )
    )
    assert match is not None
    # 让位给"修复前写入"的 P1 行：把本次常规评分结果行转为历史，再插入旧规则版本的 P1。
    for alert in _alerts_by_supplier(db_session, outcome.event_id)[target.id]:
        alert.status = "expired"
    db_session.flush()
    legacy_p1 = RiskAlert(
        match_id=match.id,
        level="P1",
        score=100,
        score_detail={
            "rule_version": "risk-score-v1-corporate-f92bd8f213a9",
            "forced_rule": {
                "name": "sanctions_entity_hit",
                "original_level": "P2",
                "original_score": 78,
            },
            "final_level": "P1",
        },
        status="current",
        expires_at=None,
        expiry_kind="unbounded",
    )
    db_session.add(legacy_p1)
    db_session.flush()

    _process(db_session, signal, result, now=_NOW_1)

    alerts = _alerts_by_supplier(db_session, outcome.event_id)[target.id]
    by_status = {alert.status: alert for alert in alerts}
    # 旧 P1 行保留为历史（不删除），且不再是 current。
    assert legacy_p1.status == "expired"
    assert legacy_p1.level == "P1"
    assert legacy_p1.score == 100
    # 新 current 行按新规则版本重算，不再是 P1。
    current = by_status["current"]
    assert current.level != "P1"
    assert current.score < 100
    assert current.score_detail["rule_version"] != legacy_p1.score_detail["rule_version"]
    assert str(current.score_detail["rule_version"]).startswith("risk-score-v1-")