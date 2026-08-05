import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.schemas import RiskProcessResult
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier
from app.suppliers.schemas import normalize_alias

SEVERITY_SCORES = {"critical": 35, "high": 28, "medium": 20, "low": 10}
RULE_VERSION = "risk-score-v0"


def event_dedup_key(result: SignalAnalysisResult) -> str:
    organizations = sorted(
        (normalize_alias(item.name), item.registry_no or "") for item in result.organizations
    )
    locations = sorted(
        (
            normalize_alias(item.name),
            item.country_code or "",
            item.region or "",
            item.city or "",
        )
        for item in result.locations
    )
    identity: dict[str, object] = {
        "type": result.event_type,
        "organizations": organizations,
        "locations": locations,
        "start_date": result.start_at.date().isoformat() if result.start_at else None,
    }
    if not organizations and not locations and result.start_at is None:
        identity["summary"] = normalize_alias(result.summary_zh)
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def risk_level(score: int) -> str:
    if score >= 85:
        return "P1"
    if score >= 65:
        return "P2"
    if score >= 40:
        return "P3"
    return "P4"


def match_suppliers(
    session: Session, result: SignalAnalysisResult
) -> list[tuple[Supplier, str, int, list[str]]]:
    suppliers = list(session.scalars(select(Supplier).where(Supplier.enabled.is_(True))))
    matches: dict[int, tuple[Supplier, str, int, list[str]]] = {}
    for organization in result.organizations:
        normalized_name = normalize_alias(organization.name)
        for supplier in suppliers:
            if (
                organization.registry_no
                and supplier.registry_no
                and organization.registry_no == supplier.registry_no
            ):
                matches[supplier.id] = (
                    supplier,
                    "registry_no",
                    30,
                    [f"注册编号精确匹配：{organization.registry_no}"],
                )
            elif (
                supplier.id not in matches
                and normalize_alias(supplier.legal_name) == normalized_name
            ):
                matches[supplier.id] = (
                    supplier,
                    "legal_name",
                    25,
                    [f"法人全称精确匹配：{organization.name}"],
                )
    return list(matches.values())


def score_alert(
    severity: str, match_score: int, credibility: int, has_published_at: bool
) -> tuple[int, dict[str, object]]:
    detail: dict[str, object] = {
        "rule_version": RULE_VERSION,
        "severity": SEVERITY_SCORES[severity],
        "association": match_score,
        "source_credibility": round(credibility * 0.2),
        "timeliness": 10 if has_published_at else 5,
        "product_relevance": 0,
    }
    total = sum(value for value in detail.values() if isinstance(value, int))
    return min(total, 100), detail


def process_analysis(
    session: Session, signal: RawSignal, analysis: AIAnalysisRecord
) -> RiskProcessResult:
    if analysis.status != "succeeded" or analysis.result is None:
        raise ValueError("AI 分析尚未成功")
    result = SignalAnalysisResult.model_validate(analysis.result)
    dedup_key = event_dedup_key(result)
    event = session.scalar(select(RiskEvent).where(RiskEvent.dedup_key == dedup_key))
    event_created = event is None
    if event is None:
        event = RiskEvent(
            dedup_key=dedup_key,
            event_type=result.event_type,
            severity=result.suggested_severity,
            summary=result.summary_zh,
            start_at=result.start_at,
            end_at=result.end_at,
            confidence=result.confidence,
            facts=result.model_dump(mode="json"),
        )
        session.add(event)
        session.flush()

    link = session.get(RiskEventSignal, (event.id, signal.id))
    signal_linked = link is None
    if link is None:
        session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))

    source = session.get(DataSource, signal.source_id)
    assert source is not None
    alert_ids: list[int] = []
    for supplier, match_type, match_score, reasons in match_suppliers(session, result):
        match = session.scalar(
            select(SupplierEventMatch).where(
                SupplierEventMatch.supplier_id == supplier.id,
                SupplierEventMatch.event_id == event.id,
            )
        )
        if match is None:
            match = SupplierEventMatch(
                supplier_id=supplier.id,
                event_id=event.id,
                match_type=match_type,
                score=match_score,
                reasons=reasons,
            )
            session.add(match)
            session.flush()

        score, score_detail = score_alert(
            result.suggested_severity,
            match.score,
            source.credibility,
            signal.published_at is not None,
        )
        alert = session.scalar(select(RiskAlert).where(RiskAlert.match_id == match.id))
        if alert is None:
            alert = RiskAlert(
                match_id=match.id,
                level=risk_level(score),
                score=score,
                score_detail=score_detail,
                status="current",
            )
            session.add(alert)
            session.flush()
        else:
            alert.level = risk_level(score)
            alert.score = score
            alert.score_detail = score_detail
            alert.updated_at = datetime.now(UTC)
        alert_ids.append(alert.id)

    session.commit()
    return RiskProcessResult(
        signal_id=signal.id,
        event_id=event.id,
        event_created=event_created,
        signal_linked=signal_linked,
        alert_ids=alert_ids,
    )
