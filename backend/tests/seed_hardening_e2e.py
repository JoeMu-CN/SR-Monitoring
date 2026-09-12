"""任务10：硬化确定性 seed（叠加 seed_e2e，仅严格守卫 supplier_risk_test）。

- 守卫：先 require_test_database_url，再要求库名严格等于 supplier_risk_test。
- 叠加幂等：以 HRD-SUP-001 为标记；已 seed 只回执不写入；从不删除或改写
  seed_e2e 的既有用户/25 供应商/2 提醒契约；账号与信源按名去重兜底。
- 规模：+100 供应商（完整别名/地点/产品资料）、+154 提醒（current 137 > 100，
  P1-P4 全覆盖，current/expired 证据齐备），含共享事件与共享 match 证据。
- 任务7：写入过期 scheduler 心跳（隔离栈无 pytest 清理 → 可观测 degraded）。
- 任务9：用纯渲染函数以 http://127.0.0.1:18080 生成可审计投递内容（含
  /risks/{id} 链接），不注入 NOTIFY_FRONTEND_URL、不修改 compose。
- 输出：机器可读 receipt（stdout JSON），供 E2E 与证据链核对。
- 纯 LOC 说明（DATA_OK）：本模块是确定性造数 fixture，绝大多数行是可枚举的
  测试数据（供应商/信号/事件/匹配/提醒/投递），逻辑仅负责编排与幂等守卫。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Final, TypedDict

from e2e_source_signal_fixtures import LEGACY_VALIDITY_REASON_JSON
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_stack_guard import UnsafeTestDatabaseError, require_test_database_url

from app.auth.models import User
from app.auth.security import hash_password
from app.database import SessionLocal, engine
from app.notification.models import NotificationDelivery
from app.notification.rendering import (
    DigestMember,
    alert_context,
    render_alert_payload,
    render_digest,
)
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.query_validity import current_alert_condition
from app.scheduler.runtime_models import SchedulerRuntimeState
from app.signals.models import DataSource, RawSignal
from app.signals.validity import ValidityState
from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct, SupplierSite

ADMIN_USERNAME: Final = "hardening-platform-admin"
VIEWER_USERNAME: Final = "hardening-viewer"
TEST_PASSWORD: Final = "Hardening-Test-Only-2026!"
REQUIRED_DATABASE: Final = "supplier_risk_test"
FRONTEND_BASE_URL: Final = "http://127.0.0.1:18080"
FIXED_NOW: Final = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
SOURCE_ID: Final = 91_500
SOURCE_CODE: Final = "hardening-evidence-source"
SUPPLIER_BASE_ID: Final = 92_500
SUPPLIER_TOTAL: Final = 100
EDIT_SUPPLIER_CODE: Final = "HRD-SUP-078"
DELETE_SUPPLIER_CODE: Final = "HRD-SUP-079"
SHARED_EVENT_DEDUP_KEY: Final = "hrd-event-shared-1"
USER_ADMIN_ID: Final = 90_101
USER_VIEWER_ID: Final = 90_102
LEVEL_SCORES: Final = {"P1": 95, "P2": 80, "P3": 60, "P4": 30}
LEVEL_SEVERITY: Final = {"P1": "critical", "P2": "high", "P3": "medium", "P4": "low"}
EVENT_TYPES: Final = ("weather", "judicial", "compliance", "logistics")
# 造数计划（纯数据）：(供应商索引区间, current 条数, 附加 expired 条数)
ALERT_PLAN: Final = ((range(60), 2, 0), (range(60, 75), 1, 1))


class HardeningSeedReceipt(TypedDict):
    database: str
    seeded: bool
    users: list[str]
    supplier_total: int
    hardening_supplier_total: int
    alert_total: int
    current_alert_total: int
    expired_alert_total: int
    current_level_counts: dict[str, int]
    delivery_total: int
    notification_link_alert_ids: list[int]
    frontend_base_url: str
    scheduler_heartbeat_seeded: bool


def _guard() -> None:
    require_test_database_url(str(engine.url))
    if engine.url.database != REQUIRED_DATABASE:
        raise UnsafeTestDatabaseError(database_name=engine.url.database)


def _level(seq: int) -> str:
    return ("P1", "P2", "P3", "P4")[(seq - 1) % 4]


def _supplier(index: int) -> Supplier:
    supplier = Supplier(
        id=SUPPLIER_BASE_ID + index, supplier_code=f"HRD-SUP-{index:03d}",
        legal_name=f"硬化供应商 {index:03d}", country_code="CN",
        registry_no=f"HRD-REG-{index:03d}", registration_address=f"硬化登记路 {index:03d} 号",
        industry="hardening-fixtures", raw_materials=["steel", "copper"],
        enabled=index != 78, created_at=FIXED_NOW, updated_at=FIXED_NOW,
    )
    supplier.aliases.append(SupplierAlias(
        alias=f"硬化别名 {index:03d}", language="zh", normalized_alias=f"硬化别名 {index:03d}"))
    supplier.sites.append(SupplierSite(
        site_name=f"硬化工厂 {index:03d}", country_code="CN", region="Shanghai",
        city="Shanghai", district="Pudong", address=f"硬化生产路 {index:03d} 号",
        latitude=31.2304, longitude=121.4737))
    supplier.products.append(SupplierProduct(
        name=f"硬化部件 {index:03d}", keywords=["hardening", f"hrd-{index:03d}"]))
    return supplier


def _alert(
    session: Session, match: SupplierEventMatch, *, level: str, status: str,
    expiry_kind: str, expires_at: datetime | None, created_at: datetime,
    alert_id: int | None = None, updated_at: datetime | None = None,
) -> RiskAlert:
    alert = RiskAlert(
        id=alert_id, match_id=match.id, level=level, score=LEVEL_SCORES[level],
        score_detail={"rule_version": "hardening-v1", "evidence": LEVEL_SCORES[level]},
        status=status, expires_at=expires_at, expiry_kind=expiry_kind,
        created_at=created_at, updated_at=updated_at or created_at,
    )
    session.add(alert)
    return alert


def _chain(
    session: Session, supplier: Supplier, *, seq: int, level: str, status: str,
    expiry_kind: str, expires_at: datetime | None, created_at: datetime,
    updated_at: datetime | None = None,
) -> RiskAlert:
    signal_id, event_id, match_id = 94_500 + seq, 93_500 + seq, 95_500 + seq
    session.add(RawSignal(
        id=signal_id, source_id=SOURCE_ID, external_id=f"HRD-SIG-{seq:04d}",
        title=f"硬化证据信号 {seq:04d}", content=f"硬化确定性证据 {seq:04d}",
        url=f"https://example.test/hardening/{seq}", published_at=created_at,
        collected_at=created_at, fingerprint=f"hardening-fp-{seq:04d}",
        raw_data={"fixture": True, "hardening": True},
        validity_state=ValidityState.LEGACY, validity_reason=LEGACY_VALIDITY_REASON_JSON,
    ))
    session.add(RiskEvent(
        id=event_id, dedup_key=f"hrd-event-{seq:04d}", event_type=EVENT_TYPES[seq % 4],
        event_subtype="hardening_probe", severity=LEVEL_SEVERITY[level],
        summary=f"硬化确定性事件 {seq:04d}", start_at=created_at, end_at=None,
        confidence=0.9, facts={"fixture": True, "sequence": seq},
    ))
    session.flush()
    session.add(RiskEventSignal(event_id=event_id, signal_id=signal_id))
    match = SupplierEventMatch(
        id=match_id, supplier_id=supplier.id, event_id=event_id,
        match_type="registry_no+site", score=LEVEL_SCORES[level],
        reasons=["registry_no_exact", "production_site_overlap"],
        evidence=[{"signal_id": signal_id, "source_code": SOURCE_CODE}],
        created_at=created_at,
    )
    session.add(match)
    session.flush()
    return _alert(
        session, match, level=level, status=status, expiry_kind=expiry_kind,
        expires_at=expires_at, created_at=created_at, updated_at=updated_at,
    )


def _shared_event(session: Session, suppliers: list[Supplier]) -> RiskAlert:
    signal_id, event_id = 94_400, 93_400
    session.add(RawSignal(
        id=signal_id, source_id=SOURCE_ID, external_id="HRD-SIG-SHARED",
        title="硬化共享证据信号", content="一份事件证据被两家供应商共享",
        url="https://example.test/hardening/shared", published_at=FIXED_NOW,
        collected_at=FIXED_NOW, fingerprint="hardening-fp-shared",
        raw_data={"fixture": True, "shared": True},
        validity_state=ValidityState.LEGACY, validity_reason=LEGACY_VALIDITY_REASON_JSON,
    ))
    session.add(RiskEvent(
        id=event_id, dedup_key=SHARED_EVENT_DEDUP_KEY, event_type="compliance",
        event_subtype="hardening_shared", severity="high", summary="硬化共享事件",
        start_at=FIXED_NOW, end_at=None, confidence=0.9, facts={"fixture": True, "shared": True},
    ))
    session.flush()
    session.add(RiskEventSignal(event_id=event_id, signal_id=signal_id))
    evidence = [{"signal_id": signal_id, "source_code": SOURCE_CODE}]
    current_match = SupplierEventMatch(
        id=95_400, supplier_id=suppliers[75].id, event_id=event_id,
        match_type="legal_name", score=95, reasons=["shared_event_current"], evidence=evidence,
    )
    expired_match = SupplierEventMatch(
        id=95_401, supplier_id=suppliers[76].id, event_id=event_id,
        match_type="legal_name", score=95, reasons=["shared_event_expired"], evidence=evidence,
    )
    session.add_all([current_match, expired_match])
    session.flush()
    expired_at = FIXED_NOW - timedelta(days=2)
    _alert(
        session, expired_match, level="P3", status="expired", expiry_kind="finite",
        expires_at=expired_at, created_at=expired_at, alert_id=98_401,
    )
    return _alert(
        session, current_match, level="P1", status="current", expiry_kind="unbounded",
        expires_at=None, created_at=FIXED_NOW, alert_id=98_400,
    )


def _notifications(session: Session, first_current: dict[int, RiskAlert]) -> None:
    for index in (0, 1, 75):
        alert = first_current[index]
        title, content = render_alert_payload(
            alert, *alert_context(session, alert), FRONTEND_BASE_URL)
        session.add(NotificationDelivery(
            alert_id=alert.id, channel="dingtalk", status="success", title=title,
            content=content, pushed_level=alert.level, delivered_at=FIXED_NOW,
            created_at=FIXED_NOW,
        ))
    members = [first_current[index] for index in (2, 3)]
    digest_members: list[DigestMember] = []
    for alert in members:
        supplier_name, event_type, _summary, _reasons = alert_context(session, alert)
        digest_members.append(DigestMember(
            level=alert.level, supplier_name=supplier_name,
            event_type=event_type, alert_id=alert.id))
    title, content = render_digest(digest_members, FRONTEND_BASE_URL)
    for position, alert in enumerate(members):
        session.add(NotificationDelivery(
            alert_id=alert.id, channel="dingtalk",
            status="success" if position == 0 else "merged", title=title, content=content,
            pushed_level=alert.level, delivered_at=FIXED_NOW, created_at=FIXED_NOW,
        ))


def _write(session: Session) -> None:
    existing = set(session.scalars(
        select(User.username).where(User.username.in_([ADMIN_USERNAME, VIEWER_USERNAME]))))
    accounts = (
        (USER_ADMIN_ID, ADMIN_USERNAME, "platform_admin", "硬化平台管理员"),
        (USER_VIEWER_ID, VIEWER_USERNAME, "viewer", "硬化只读用户"),
    )
    for user_id, username, role, display_name in accounts:
        if username not in existing:
            session.add(User(
                id=user_id, username=username, password_hash=hash_password(TEST_PASSWORD),
                display_name=display_name, role=role, status="active",
                password_changed_at=FIXED_NOW,
            ))
    if session.scalar(select(DataSource.id).where(DataSource.code == SOURCE_CODE)) is None:
        session.add(DataSource(
            id=SOURCE_ID, code=SOURCE_CODE, name="硬化证据信源", source_type="official",
            credibility=90, endpoint_url="https://example.test/hardening-feed",
            auth_type="none", login_config={}, adapter_config={}, adapter_status="builtin",
            adapter_version=1, enabled=True, signal_validity_days=10,
            created_at=FIXED_NOW, updated_at=FIXED_NOW,
        ))
    suppliers = [_supplier(index) for index in range(1, SUPPLIER_TOTAL + 1)]
    session.add_all(suppliers)
    session.flush()
    expired_at = FIXED_NOW - timedelta(days=2)
    first_current: dict[int, RiskAlert] = {}
    seq = 0
    for offsets, current_count, expired_count in ALERT_PLAN:
        for offset in offsets:
            for half in range(current_count):
                seq += 1
                alert = _chain(
                    session, suppliers[offset], seq=seq, level=_level(seq), status="current",
                    expiry_kind="unbounded" if half == 0 else "finite",
                    expires_at=None if half == 0 else FIXED_NOW + timedelta(days=300),
                    created_at=FIXED_NOW if half == 0 else FIXED_NOW - timedelta(days=40),
                )
                first_current.setdefault(offset, alert)
            for _ in range(expired_count):
                seq += 1
                _chain(
                    session, suppliers[offset], seq=seq, level=_level(seq), status="expired",
                    expiry_kind="finite", expires_at=expired_at, created_at=expired_at,
                    updated_at=expired_at,
                )
    first_current[75] = _shared_event(session, suppliers)
    blocked = _chain(
        session, suppliers[78], seq=151, level="P1", status="current",
        expiry_kind="unbounded", expires_at=None, created_at=FIXED_NOW,
    )
    blocked_match = session.get(SupplierEventMatch, blocked.match_id)
    assert blocked_match is not None
    _alert(
        session, blocked_match, level="P2", status="expired", expiry_kind="finite",
        expires_at=expired_at, created_at=expired_at, alert_id=98_654,
    )
    _notifications(session, first_current)
    stale_at = FIXED_NOW - timedelta(hours=1)
    session.add(SchedulerRuntimeState(
        job_key="scheduler", status="succeeded", heartbeat_at=stale_at,
        last_success_at=stale_at, last_finished_at=stale_at,
    ))


def _receipt(session: Session, *, seeded: bool) -> HardeningSeedReceipt:
    current = current_alert_condition(datetime.now(UTC))
    levels = dict(session.execute(
        select(RiskAlert.level, func.count()).where(current).group_by(RiskAlert.level)).all())
    link_ids = list(session.scalars(
        select(NotificationDelivery.alert_id)
        .where(NotificationDelivery.content.contains(f"{FRONTEND_BASE_URL}/risks/")).distinct()))
    return {
        "database": engine.url.database or "",
        "seeded": seeded,
        "users": [ADMIN_USERNAME, VIEWER_USERNAME],
        "supplier_total": session.scalar(select(func.count()).select_from(Supplier)) or 0,
        "hardening_supplier_total": session.scalar(
            select(func.count()).select_from(Supplier)
            .where(Supplier.supplier_code.like("HRD-SUP-%"))) or 0,
        "alert_total": session.scalar(select(func.count()).select_from(RiskAlert)) or 0,
        "current_alert_total": session.scalar(
            select(func.count()).select_from(RiskAlert).where(current)) or 0,
        "expired_alert_total": session.scalar(
            select(func.count()).select_from(RiskAlert)
            .where(RiskAlert.status == "expired")) or 0,
        "current_level_counts": levels,
        "delivery_total": session.scalar(
            select(func.count()).select_from(NotificationDelivery)) or 0,
        "notification_link_alert_ids": sorted(link_ids),
        "frontend_base_url": FRONTEND_BASE_URL,
        "scheduler_heartbeat_seeded": session.scalar(
            select(func.count()).select_from(SchedulerRuntimeState)
            .where(SchedulerRuntimeState.job_key == "scheduler")) == 1,
    }


def seed() -> HardeningSeedReceipt:
    _guard()
    with SessionLocal.begin() as session:
        # 幂等标记与 seed_e2e 一致：硬化管理员已存在即视为已叠加，只回执不写入。
        seeded = session.scalar(
            select(User.id).where(User.username == ADMIN_USERNAME)) is None
        if seeded:
            _write(session)
        return _receipt(session, seeded=seeded)


if __name__ == "__main__":
    print(json.dumps(seed(), ensure_ascii=False, sort_keys=True))
