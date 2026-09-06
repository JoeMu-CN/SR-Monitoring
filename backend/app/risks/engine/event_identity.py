"""风险事件身份计算、冲突安全创建与事实持久化。"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.schemas import SignalAnalysisResult
from app.risks.models import EventEntity, EventLocation, RiskEvent
from app.suppliers.schemas import normalize_alias


def event_dedup_key(result: SignalAnalysisResult) -> str:
    organizations = sorted(
        (normalize_alias(item.name), item.registry_no or "") for item in result.organizations
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


def find_or_create_event(
    session: Session, result: SignalAnalysisResult
) -> tuple[RiskEvent, bool]:
    """创建事件；唯一键并发冲突时回滚保存点并重查复用赢家。"""
    dedup_key = event_dedup_key(result)
    existing = session.scalar(select(RiskEvent).where(RiskEvent.dedup_key == dedup_key))
    if existing is not None:
        return existing, False
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
        validity_state="active",
        validity_reason={
            "code": "effective_signal_support",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        winner = session.scalar(select(RiskEvent).where(RiskEvent.dedup_key == dedup_key))
        if winner is None:
            raise
        return winner, False
    return event, True


def persist_event_facts(
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
                    district=location.district,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    radius_km=location.radius_km,
                )
            )
