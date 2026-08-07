import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.orm import Session, selectinload

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
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
from app.suppliers.models import Supplier, SupplierSite
from app.suppliers.schemas import normalize_alias

MATCH_ORDER = {
    "registry_no": 0,
    "legal_name": 1,
    "alias": 2,
    "site_distance": 3,
    "site_text": 4,
    "product": 5,
}


@dataclass
class MatchCandidate:
    supplier: Supplier
    match_types: set[str] = field(default_factory=set)
    association_score: int = 0
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, object]] = field(default_factory=list)

    def add(
        self,
        match_type: str,
        score: int,
        reason: str,
        evidence: dict[str, object],
    ) -> None:
        self.match_types.add(match_type)
        self.association_score = max(self.association_score, score)
        if reason not in self.reasons:
            self.reasons.append(reason)
        if evidence not in self.evidence:
            self.evidence.append(evidence)


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
        "organizations": organizations,
        "locations": locations,
        "start_date": result.start_at.date().isoformat() if result.start_at else None,
    }
    if not organizations and not locations and result.start_at is None:
        identity["summary"] = normalize_alias(result.summary_zh)
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _candidate(
    matches: dict[int, MatchCandidate], supplier: Supplier
) -> MatchCandidate:
    return matches.setdefault(supplier.id, MatchCandidate(supplier=supplier))


def _spatial_site_distances(
    session: Session, *, latitude: float, longitude: float, radius_km: float,
    country_code: str | None,
) -> dict[int, float]:
    rows = session.execute(
        text(
            """
            SELECT id,
                   ST_Distance(
                       geom,
                       ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
                   ) / 1000.0 AS distance_km
            FROM supplier_sites
            WHERE geom IS NOT NULL
              AND (
                  CAST(:country_code AS text) IS NULL
                  OR country_code = CAST(:country_code AS text)
              )
              AND ST_DWithin(
                  geom,
                  ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                  :radius_meters
              )
            """
        ),
        {
            "latitude": latitude,
            "longitude": longitude,
            "country_code": country_code,
            "radius_meters": radius_km * 1000,
        },
    ).mappings()
    return {int(row["id"]): float(row["distance_km"]) for row in rows}


def _matches_text_location(location_values: list[str], site: SupplierSite) -> bool:
    site_values = {
        normalize_alias(value)
        for value in (site.site_name, site.region, site.city)
        if value
    }
    normalized_address = normalize_alias(site.address)
    return any(
        value in site_values or (len(value) >= 2 and value in normalized_address)
        for value in location_values
    )


def match_suppliers(
    session: Session,
    result: SignalAnalysisResult,
    scoring: ScoringSettings | None = None,
) -> list[MatchCandidate]:
    if scoring is None:
        scoring = load_scoring_settings()
    assoc = scoring.association_scores
    suppliers = list(
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
    matches: dict[int, MatchCandidate] = {}
    for organization in result.organizations:
        normalized_name = normalize_alias(organization.name)
        for supplier in suppliers:
            if (
                organization.registry_no
                and supplier.registry_no
                and organization.registry_no == supplier.registry_no
            ):
                _candidate(matches, supplier).add(
                    "registry_no",
                    assoc.get("registry_no", 30),
                    f"注册编号精确匹配：{organization.registry_no}",
                    {
                        "object_type": "supplier",
                        "supplier_id": supplier.id,
                        "registry_no": organization.registry_no,
                    },
                )
            elif normalize_alias(supplier.legal_name) == normalized_name:
                _candidate(matches, supplier).add(
                    "legal_name",
                    assoc.get("legal_name", 25),
                    f"法人全称精确匹配：{organization.name}",
                    {
                        "object_type": "supplier",
                        "supplier_id": supplier.id,
                        "legal_name": supplier.legal_name,
                    },
                )
            else:
                alias = next(
                    (
                        item
                        for item in supplier.aliases
                        if item.normalized_alias == normalized_name
                    ),
                    None,
                )
                if alias is not None:
                    _candidate(matches, supplier).add(
                        "alias",
                        assoc.get("alias", 25),
                        f"供应商别名精确匹配：{alias.alias}",
                        {
                            "object_type": "alias",
                            "alias_id": alias.id,
                            "alias": alias.alias,
                        },
                    )

    site_by_id = {
        site.id: (supplier, site)
        for supplier in suppliers
        for site in supplier.sites
    }
    for location in result.locations:
        location_values = list(
            dict.fromkeys(
                normalize_alias(value)
                for value in (location.name, location.region, location.city)
                if value
            )
        )
        for supplier in suppliers:
            for site in supplier.sites:
                if location.country_code and site.country_code != location.country_code:
                    continue
                if _matches_text_location(location_values, site):
                    _candidate(matches, supplier).add(
                        "site_text",
                        assoc.get("site_text", 20),
                        f"事件地点与生产地点匹配：{location.name} → {site.site_name}",
                        {
                            "object_type": "site",
                            "site_id": site.id,
                            "site_name": site.site_name,
                            "event_location": location.name,
                            "method": "text",
                        },
                    )

        if (
            location.latitude is not None
            and location.longitude is not None
            and location.radius_km is not None
        ):
            # ponytail: 单个事件地点通常很少；超过 10 个时改为 VALUES 批量空间查询。
            distances = _spatial_site_distances(
                session,
                latitude=location.latitude,
                longitude=location.longitude,
                radius_km=location.radius_km,
                country_code=location.country_code,
            )
            for site_id, distance_km in distances.items():
                supplier_site = site_by_id.get(site_id)
                if supplier_site is None:
                    continue
                supplier, site = supplier_site
                reason = (
                    f"生产地点距事件中心 {distance_km:.1f} km，"
                    f"位于 {location.radius_km:g} km 影响范围内"
                )
                _candidate(matches, supplier).add(
                    "site_distance",
                    assoc.get("site_distance", 20),
                    reason,
                    {
                        "object_type": "site",
                        "site_id": site.id,
                        "site_name": site.site_name,
                        "event_location": location.name,
                        "method": "distance",
                        "distance_km": round(distance_km, 2),
                        "radius_km": location.radius_km,
                    },
                )

    affected_products = [normalize_alias(item) for item in result.affected_products]
    for supplier in suppliers:
        for product in supplier.products:
            terms = [normalize_alias(product.name), *map(normalize_alias, product.keywords)]
            matched_term = next(
                (
                    term
                    for term in terms
                    for affected in affected_products
                    if len(term) >= 2 and (term in affected or affected in term)
                ),
                None,
            )
            if matched_term is not None:
                _candidate(matches, supplier).add(
                    "product",
                    assoc.get("product", 12),
                    f"受影响产品关键词匹配：{matched_term} → {product.name}",
                    {
                        "object_type": "product",
                        "product_id": product.id,
                        "product_name": product.name,
                        "keyword": matched_term,
                    },
                )

    return [matches[key] for key in sorted(matches)]


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


def _compute_expires_at(
    event: RiskEvent, scoring: ScoringSettings
) -> datetime:
    """计算提醒失效时间。

    有事件结束时间时，在结束后保留 alert_expiry_days 天；
    无结束时间时，从当前时间起保留 alert_expiry_days 天。
    """
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


def process_analysis(
    session: Session,
    signal: RawSignal,
    analysis: AIAnalysisRecord,
    scoring: ScoringSettings | None = None,
) -> RiskProcessResult:
    if scoring is None:
        scoring = load_scoring_settings()
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
    _persist_event_facts(session, event, result)

    link = session.get(RiskEventSignal, (event.id, signal.id))
    signal_linked = link is None
    if link is None:
        session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))

    source = session.get(DataSource, signal.source_id)
    assert source is not None
    alert_ids: list[int] = []
    for candidate in match_suppliers(session, result, scoring):
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
        level = compute_level(scoring, score)
        level = apply_level_cap(scoring, level, match.match_type, score_detail)
        level, score = apply_forced_rules(
            scoring, event.event_type, match.match_type, level, score, score_detail
        )
        expires_at = _compute_expires_at(event, scoring)
        alert = session.scalar(select(RiskAlert).where(RiskAlert.match_id == match.id))
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
