"""规则引擎编排器。

职责：事件归并 → 按事件类型分派到启用的维度模块 → 执行该维度声明的
匹配柱收集候选 → 用该维度的评分参数定级（强制规则 + 弱关联上限）→
生成/更新风险提醒。引擎本身不含业务规则，全部业务规则在维度模块与
scoring 参数中，可独立维护并运行时启停、调参。

兼容性：match_suppliers 保留为"主体+地点+产品"三柱的兼容包装（与旧
service.match_suppliers 行为一致）；corporate/natural/industry 维度默认
配置复现现有评分行为，既有测试与数据不受影响。
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session, selectinload

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.risks.engine.config import (
    COLUMN_COUNTRY,
    COLUMN_ENTITY,
    COLUMN_INDUSTRY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
)
from app.risks.engine.matching import (
    MATCH_ORDER,
    MatchCandidate,
    match_countries,
    match_entities,
    match_industries,
    match_locations,
    match_products,
)
from app.risks.engine.registry import RuntimeDimension, load_dimensions
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.schemas import RiskProcessResult
from app.risks.scoring import (
    ScoringSettings,
    apply_forced_rules,
    apply_level_cap,
    compute_level,
    compute_score,
    load_scoring_settings,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier
from app.suppliers.schemas import normalize_alias

MatcherFn = Callable[
    [Session, SignalAnalysisResult, list[Supplier], dict[str, int], dict[int, MatchCandidate]],
    None,
]

MATCHERS: dict[str, MatcherFn] = {
    COLUMN_ENTITY: match_entities,
    COLUMN_LOCATION: match_locations,
    COLUMN_PRODUCT: match_products,
    COLUMN_COUNTRY: match_countries,
    COLUMN_INDUSTRY: match_industries,
}


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
            round(item.latitude, 6) if item.latitude is not None else None,
            round(item.longitude, 6) if item.longitude is not None else None,
            item.radius_km,
        )
        for item in result.locations
    )
    identity: dict[str, object] = {
        "type": result.event_type,
        "subtype": result.event_subtype,
        "organizations": organizations,
        "locations": locations,
        "start_date": result.start_at.date().isoformat() if result.start_at else None,
    }
    if not organizations and not locations and result.start_at is None:
        identity["summary"] = normalize_alias(result.summary_zh)
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _match_type(types: set[str]) -> str:
    return "+".join(sorted(types, key=MATCH_ORDER.__getitem__))


def _persist_event_facts(
    session: Session, event: RiskEvent, result: SignalAnalysisResult
) -> None:
    for organization in result.organizations:
        normalized_name = normalize_alias(organization.name)
        exists = session.scalar(
            select(EventEntity.id).where(
                EventEntity.event_id == event.id,
                EventEntity.normalized_name == normalized_name,
            )
        )
        if exists is None:
            session.add(
                EventEntity(
                    event_id=event.id,
                    name=organization.name,
                    normalized_name=normalized_name,
                    registry_no=organization.registry_no,
                )
            )
    for location in result.locations:
        normalized_name = normalize_alias(location.name)
        exists = session.scalar(
            select(EventLocation.id).where(
                EventLocation.event_id == event.id,
                EventLocation.normalized_name == normalized_name,
            )
        )
        if exists is None:
            session.add(
                EventLocation(
                    event_id=event.id,
                    name=location.name,
                    normalized_name=normalized_name,
                    country_code=location.country_code,
                    region=location.region,
                    city=location.city,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    radius_km=location.radius_km,
                )
            )


def _compute_expires_at(event: RiskEvent, scoring: ScoringSettings) -> datetime:
    base = event.end_at or datetime.now(UTC)
    return base + timedelta(days=scoring.alert_expiry_days)


def expire_alerts(session: Session) -> int:
    """将超过 expires_at 的 current 提醒标记为 expired，返回失效条数。"""
    now = datetime.now(UTC)
    result = session.execute(
        update(RiskAlert)
        .where(
            RiskAlert.status == "current",
            RiskAlert.expires_at.is_not(None),
            RiskAlert.expires_at < now,
        )
        .values(status="expired", updated_at=now)
    )
    if not isinstance(result, CursorResult):
        return 0
    return result.rowcount if result.rowcount is not None else 0


def _load_suppliers(session: Session) -> list[Supplier]:
    return list(
        session.scalars(
            select(Supplier)
            .where(Supplier.enabled.is_(True))
            .options(
                selectinload(Supplier.aliases),
                selectinload(Supplier.sites),
                selectinload(Supplier.products),
            )
        ).unique()
    )


def match_suppliers(
    session: Session,
    result: SignalAnalysisResult,
    scoring: ScoringSettings | None = None,
) -> list[MatchCandidate]:
    """兼容包装：主体+地点+产品三柱匹配（与旧 service.match_suppliers 一致）。"""
    if scoring is None:
        scoring = load_scoring_settings()
    assoc = scoring.association_scores
    suppliers = _load_suppliers(session)
    matches: dict[int, MatchCandidate] = {}
    match_entities(session, result, suppliers, assoc, matches)
    match_locations(session, result, suppliers, assoc, matches)
    match_products(session, result, suppliers, assoc, matches)
    return [matches[key] for key in sorted(matches)]


def _resolve_dimension(
    dimensions: list[RuntimeDimension], event_type: str
) -> RuntimeDimension | None:
    """按事件类型分派到第一个启用且接管的维度（各维度事件类型互斥）。"""
    return next(
        (d for d in dimensions if d.enabled and d.handles(event_type)), None
    )


def process_event(
    session: Session,
    signal: RawSignal,
    analysis: AIAnalysisRecord,
) -> RiskProcessResult:
    """引擎主流程：归并事件 → 维度分派 → 匹配 → 评分 → 提醒。"""
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
            event_subtype=result.event_subtype,
            severity=result.suggested_severity,
            summary=result.summary_zh,
            start_at=result.start_at,
            end_at=result.end_at,
            confidence=result.confidence,
            facts=result.model_dump(mode="json"),
        )
        session.add(event)
        session.flush()
    _persist_event_facts(session, event, result)

    link = session.get(RiskEventSignal, (event.id, signal.id))
    signal_linked = link is None
    if link is None:
        session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))

    source = session.get(DataSource, signal.source_id)
    assert source is not None

    dimensions = load_dimensions(session)
    dimension = _resolve_dimension(dimensions, result.event_type)

    alert_ids: list[int] = []
    if dimension is not None:
        scoring = dimension.scoring
        suppliers = _load_suppliers(session)
        matches: dict[int, MatchCandidate] = {}
        for column in dimension.config.match_columns:
            matcher = MATCHERS.get(column)
            if matcher is not None:
                matcher(session, result, suppliers, scoring.association_scores, matches)

        for candidate in (matches[key] for key in sorted(matches)):
            match = session.scalar(
                select(SupplierEventMatch).where(
                    SupplierEventMatch.supplier_id == candidate.supplier.id,
                    SupplierEventMatch.event_id == event.id,
                )
            )
            if match is None:
                match = SupplierEventMatch(
                    supplier_id=candidate.supplier.id,
                    event_id=event.id,
                    match_type=_match_type(candidate.match_types),
                    score=candidate.association_score,
                    reasons=candidate.reasons,
                    evidence=candidate.evidence,
                )
                session.add(match)
                session.flush()
            else:
                combined_types = set(match.match_type.split("+")) | candidate.match_types
                match.match_type = _match_type(combined_types)
                match.score = max(match.score, candidate.association_score)
                match.reasons = list(dict.fromkeys([*match.reasons, *candidate.reasons]))
                match.evidence = [
                    *match.evidence,
                    *(item for item in candidate.evidence if item not in match.evidence),
                ]

            product_relevant = any(
                item.get("object_type") == "product" for item in match.evidence
            )
            score, score_detail = compute_score(
                scoring,
                result.suggested_severity,
                match.score,
                source.credibility,
                signal.published_at is not None,
                product_relevant,
            )
            score_detail["dimension"] = dimension.key
            level = compute_level(scoring, score)
            level = apply_level_cap(scoring, level, match.match_type, score_detail)
            level, score = apply_forced_rules(
                scoring,
                event.event_type,
                match.match_type,
                level,
                score,
                score_detail,
                event_subtype=result.event_subtype,
            )
            expires_at = _compute_expires_at(event, scoring)
            alert = session.scalar(
                select(RiskAlert).where(
                    RiskAlert.match_id == match.id,
                    RiskAlert.status == "current",
                )
            )
            if (
                alert is not None
                and alert.score_detail.get("rule_version") != score_detail["rule_version"]
            ):
                alert.status = "expired"
                alert.updated_at = datetime.now(UTC)
                session.flush()
                alert = None
            if alert is None:
                alert = session.scalar(
                    select(RiskAlert)
                    .where(
                        RiskAlert.match_id == match.id,
                        RiskAlert.status == "expired",
                        RiskAlert.score_detail["rule_version"].astext
                        == str(score_detail["rule_version"]),
                    )
                    .order_by(RiskAlert.updated_at.desc(), RiskAlert.id.desc())
                )
            if alert is None:
                alert = RiskAlert(
                    match_id=match.id,
                    level=level,
                    score=score,
                    score_detail=score_detail,
                    status="current",
                    expires_at=expires_at,
                )
                session.add(alert)
                session.flush()
            else:
                alert.level = level
                alert.score = score
                alert.score_detail = score_detail
                alert.expires_at = expires_at
                alert.status = "current"
                alert.updated_at = datetime.now(UTC)
            alert_ids.append(alert.id)

    expire_alerts(session)
    session.commit()
    return RiskProcessResult(
        signal_id=signal.id,
        event_id=event.id,
        event_created=event_created,
        signal_linked=signal_linked,
        alert_ids=alert_ids,
    )


def evaluate_event(
    session: Session,
    result: SignalAnalysisResult,
    *,
    credibility: int = 80,
    has_published_at: bool = True,
) -> dict[str, object]:
    """沙箱评估：按当前维度配置执行匹配与评分，但不落库。

    供可视化工作台的"规则测试"使用：构造样例事件，实时查看维度分派、
    匹配柱命中、评分明细与最终等级，用于在保存配置前验证规则效果。
    """
    dimensions = load_dimensions(session)
    dimension = _resolve_dimension(dimensions, result.event_type)
    if dimension is None:
        return {
            "dimension": None,
            "message": f"没有启用的维度接管事件类型 {result.event_type}",
            "candidates": [],
        }
    scoring = dimension.scoring
    suppliers = _load_suppliers(session)
    matches: dict[int, MatchCandidate] = {}
    for column in dimension.config.match_columns:
        matcher = MATCHERS.get(column)
        if matcher is not None:
            matcher(session, result, suppliers, scoring.association_scores, matches)

    candidates: list[dict[str, object]] = []
    for candidate in (matches[key] for key in sorted(matches)):
        match_type = _match_type(candidate.match_types)
        product_relevant = any(
            item.get("object_type") == "product" for item in candidate.evidence
        )
        score, score_detail = compute_score(
            scoring,
            result.suggested_severity,
            candidate.association_score,
            credibility,
            has_published_at,
            product_relevant,
        )
        score_detail["dimension"] = dimension.key
        level = compute_level(scoring, score)
        level = apply_level_cap(scoring, level, match_type, score_detail)
        level, score = apply_forced_rules(
            scoring,
            result.event_type,
            match_type,
            level,
            score,
            score_detail,
            event_subtype=result.event_subtype,
        )
        candidates.append(
            {
                "supplier_id": candidate.supplier.id,
                "supplier_name": candidate.supplier.legal_name,
                "match_type": match_type,
                "association_score": candidate.association_score,
                "reasons": candidate.reasons,
                "score": score,
                "level": level,
                "score_detail": score_detail,
            }
        )
    def _sort_key(item: dict[str, object]) -> tuple[int, str]:
        score = item["score"]
        return (-(score if isinstance(score, int) else 0), str(item["supplier_name"]))

    candidates.sort(key=_sort_key)
    return {
        "dimension": {
            "key": dimension.key,
            "label": dimension.config.label,
            "match_columns": list(dimension.config.match_columns),
        },
        "candidates": candidates,
    }
