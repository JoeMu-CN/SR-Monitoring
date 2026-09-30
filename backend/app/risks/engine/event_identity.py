"""风险事件身份计算、冲突安全创建与事实持久化。"""

import hashlib
import json

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.schemas import LocationReference, OrganizationReference, SignalAnalysisResult
from app.risks.models import EventEntity, EventLocation, RiskEvent
from app.suppliers.schemas import normalize_alias


def event_dedup_key(
    result: SignalAnalysisResult, identity_override: str | None = None
) -> str:
    """计算事件去重键。

    identity_override 是服务端确定性身份（如 ``supplier_profile:<supplier_code>``），
    非空时按其单独哈希，与 AI 提取的 organizations/locations/日期及周期无关——
    保证同一供应商跨周恒同键、不因 LLM 抽取波动拆周。普通事件（None）沿用
    原身份逻辑，保证既有事件不迁移。
    """
    if identity_override is not None:
        override = identity_override.strip()
        if not override:
            raise ValueError("identity_override 不能为空白")
        return hashlib.sha256(f"identity_override:{override}".encode()).hexdigest()
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
    session: Session,
    result: SignalAnalysisResult,
    identity_override: str | None = None,
) -> tuple[RiskEvent, bool]:
    """创建事件；唯一键并发冲突时回滚保存点并重查复用赢家。

    identity_override 非空时按服务端确定性身份去重（如
    ``supplier_profile:<supplier_code>``，不含周次）；命中已有事件（含并发
    冲突后的赢家）时用当前分析刷新快照字段，避免沿用首周分类与旧 facts。
    """
    dedup_key = event_dedup_key(result, identity_override)
    refresh = identity_override is not None
    existing = session.scalar(select(RiskEvent).where(RiskEvent.dedup_key == dedup_key))
    if existing is not None:
        if refresh:
            _refresh_result_fields(existing, result)
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
        if refresh:
            _refresh_result_fields(winner, result)
        return winner, False
    return event, True


def _refresh_result_fields(event: RiskEvent, result: SignalAnalysisResult) -> None:
    """跨周复用画像事件：用当前分析刷新快照字段（dedup_key 与有效期坐标不动）。"""
    event.event_type = result.event_type
    event.event_subtype = result.event_subtype
    event.severity = result.suggested_severity
    event.summary = result.summary_zh
    event.start_at = result.start_at
    event.end_at = result.end_at
    event.confidence = result.confidence
    event.facts = result.model_dump(mode="json")


def persist_event_facts(
    session: Session,
    event: RiskEvent,
    result: SignalAnalysisResult,
    *,
    replace: bool = False,
) -> None:
    """把分析结果中的主体/地点写入事件侧表。

    replace=False（默认）：只增量补齐（普通事件的既有语义，旧行一律保留）；
    replace=True（画像 identity_override 路径）：侧表精确反映当前 result——
    删除不再出现的旧行、刷新同名行字段、补齐新行；当前集合为空时清空旧行。
    """
    if replace:
        _replace_entities(session, event, result.organizations)
        _replace_locations(session, event, result.locations)
        return
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


def _replace_entities(
    session: Session, event: RiskEvent, organizations: list[OrganizationReference]
) -> None:
    """用当前主体集合精确替换事件实体行（同名保留并刷新字段，去重取首次出现）。"""
    current: dict[str, OrganizationReference] = {}
    for organization in organizations:
        current.setdefault(normalize_alias(organization.name), organization)
    existing = {
        row.normalized_name: row
        for row in session.scalars(
            select(EventEntity).where(EventEntity.event_id == event.id)
        )
    }
    stale_ids = [row.id for name, row in existing.items() if name not in current]
    if stale_ids:
        # 先删除并 flush，避免同事务内删除与后续插入争用唯一约束。
        session.execute(delete(EventEntity).where(EventEntity.id.in_(stale_ids)))
        session.flush()
    for name, organization in current.items():
        row = existing.get(name)
        if row is None:
            session.add(
                EventEntity(
                    event_id=event.id,
                    name=organization.name,
                    normalized_name=name,
                    registry_no=organization.registry_no,
                )
            )
        else:
            row.name = organization.name
            row.registry_no = organization.registry_no


def _replace_locations(
    session: Session, event: RiskEvent, locations: list[LocationReference]
) -> None:
    """用当前地点集合精确替换事件地点行（同名保留并刷新字段，去重取首次出现）。"""
    current: dict[str, LocationReference] = {}
    for location in locations:
        current.setdefault(normalize_alias(location.name), location)
    existing = {
        row.normalized_name: row
        for row in session.scalars(
            select(EventLocation).where(EventLocation.event_id == event.id)
        )
    }
    stale_ids = [row.id for name, row in existing.items() if name not in current]
    if stale_ids:
        session.execute(delete(EventLocation).where(EventLocation.id.in_(stale_ids)))
        session.flush()
    for name, location in current.items():
        row = existing.get(name)
        if row is None:
            session.add(
                EventLocation(
                    event_id=event.id,
                    name=location.name,
                    normalized_name=name,
                    country_code=location.country_code,
                    region=location.region,
                    city=location.city,
                    district=location.district,
                    latitude=location.latitude,
                    longitude=location.longitude,
                    radius_km=location.radius_km,
                )
            )
        else:
            row.name = location.name
            row.country_code = location.country_code
            row.region = location.region
            row.city = location.city
            row.district = location.district
            row.latitude = location.latitude
            row.longitude = location.longitude
            row.radius_km = location.radius_km
