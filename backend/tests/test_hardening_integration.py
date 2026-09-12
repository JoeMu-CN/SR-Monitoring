"""任务10：硬化真实集成回归——真实 Session、真实 API 与真实服务函数。

前置：隔离栈已执行 alembic + seed_e2e，脚本阶段显式执行 seed_hardening_e2e；
模块级 fixture 再幂等调用一次（严格限定 supplier_risk_test）。seed 时间锚固定
2026-09-01，断言只用规模下限与相对阈值。API 会话经真实 /auth/login 获取。
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from seed_hardening_e2e import (
    ADMIN_USERNAME,
    DELETE_SUPPLIER_CODE,
    EDIT_SUPPLIER_CODE,
    FRONTEND_BASE_URL,
    REQUIRED_DATABASE,
    SHARED_EVENT_DEDUP_KEY,
    SOURCE_ID,
    TEST_PASSWORD,
    VIEWER_USERNAME,
    HardeningSeedReceipt,
    seed,
)
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import CSRF_COOKIE_NAME, RetentionSettings
from app.database import SessionLocal, get_session
from app.main import app
from app.notification.models import NotificationDelivery
from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.query_validity import current_alert_condition
from app.scheduler.retention import cleanup_retention
from app.scheduler.runtime_models import SchedulerRuntimeState
from app.signals.models import RawSignal
from app.suppliers.models import Supplier

ORIGIN = "http://testserver"
NOW = datetime.now(UTC)
EDIT_FIELDS = (
    "legal_name", "country_code", "registry_no", "registration_address",
    "raw_materials", "enabled", "aliases", "sites", "products",
)


@pytest.fixture(scope="module")
def receipt() -> HardeningSeedReceipt:
    return seed()


def _count(session: Session, model: type, *conditions: object) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


def _supplier_id(code: str) -> int:
    with SessionLocal() as session:
        supplier_id = session.scalar(select(Supplier.id).where(Supplier.supplier_code == code))
    assert supplier_id is not None, code
    return supplier_id


@contextmanager
def _api_client(username: str | None) -> Iterator[TestClient]:
    def fresh_session() -> Generator[Session]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = fresh_session
    try:
        with TestClient(app) as client:
            if username is not None:
                login = client.post(
                    "/api/v1/auth/login", headers={"Origin": ORIGIN},
                    json={"username": username, "password": TEST_PASSWORD},
                )
                assert login.status_code == 200, login.text
                client.headers["Origin"] = ORIGIN
                client.headers["X-CSRF-Token"] = client.cookies.get(CSRF_COOKIE_NAME, "")
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def admin_client(receipt: HardeningSeedReceipt) -> Iterator[TestClient]:
    with _api_client(ADMIN_USERNAME) as client:
        yield client


def test_seed_receipt_meets_hardening_scale_contract(receipt: HardeningSeedReceipt) -> None:
    # Then：真实 Session 复核规模、级别覆盖与共享证据结构下限。
    assert receipt["database"] == REQUIRED_DATABASE
    with SessionLocal() as session:
        assert _count(session, Supplier) >= 125
        assert _count(session, Supplier, Supplier.supplier_code.like("HRD-SUP-%")) >= 100
        assert _count(session, RiskAlert) >= 135
        current = _count(session, RiskAlert, current_alert_condition(NOW))
        assert current > 100 and current == receipt["current_alert_total"]
        assert _count(session, RiskAlert, RiskAlert.status == "expired") >= 15
        levels = dict(session.execute(
            select(RiskAlert.level, func.count())
            .where(current_alert_condition(NOW)).group_by(RiskAlert.level)).all())
        assert set(levels) == {"P1", "P2", "P3", "P4"} and min(levels.values()) >= 30
        shared = session.scalar(
            select(RiskEvent).where(RiskEvent.dedup_key == SHARED_EVENT_DEDUP_KEY))
        assert shared is not None
        matches = list(session.scalars(
            select(SupplierEventMatch.id).where(SupplierEventMatch.event_id == shared.id)))
        assert len(matches) == 2
        assert _count(session, RiskAlert, RiskAlert.match_id.in_(matches)) == 2
        assert _count(session, RiskEventSignal, RiskEventSignal.event_id == shared.id) == 1
        blocked = session.scalar(
            select(Supplier.id).where(Supplier.supplier_code == DELETE_SUPPLIER_CODE))
        assert blocked is not None
        blocked_alerts = _count(session, RiskAlert, RiskAlert.match_id.in_(
            select(SupplierEventMatch.id).where(SupplierEventMatch.supplier_id == blocked)))
        assert blocked_alerts == 2


def test_seed_is_idempotent_and_preserves_existing_contract(receipt: HardeningSeedReceipt) -> None:
    # Given：seed 已叠加
    def snapshot(session: Session) -> dict[str, int]:
        return {
            "suppliers": _count(session, Supplier),
            "alerts": _count(session, RiskAlert),
            "deliveries": _count(session, NotificationDelivery),
            "hardening": _count(session, Supplier, Supplier.supplier_code.like("HRD-SUP-%")),
        }

    with SessionLocal() as session:
        before = snapshot(session)
    # When：再次执行 seed
    second = seed()
    # Then：只回执不写入；既有 e2e 用户/25 供应商/2 提醒契约完整
    assert second["seeded"] is False
    with SessionLocal() as session:
        assert snapshot(session) == before
        codes = set(session.scalars(
            select(Supplier.supplier_code).where(Supplier.supplier_code.like("E2E-SUP-%"))))
        assert codes == {f"E2E-SUP-{index:03d}" for index in range(1, 26)}
        users = set(session.execute(select(User.username, User.role).where(
            User.username.in_(
                [ADMIN_USERNAME, VIEWER_USERNAME, "e2e-platform-admin", "e2e-viewer"]))).all())
        assert {("e2e-platform-admin", "platform_admin"), ("e2e-viewer", "viewer")} <= users
        e2e_alerts = dict(session.execute(
            select(RiskAlert.id, RiskAlert.status).where(RiskAlert.id.in_([98_001, 98_002]))).all())
        assert e2e_alerts == {98_001: "current", 98_002: "expired"}


def test_dashboard_summary_counts_fully_beyond_100(admin_client: TestClient) -> None:
    # When：真实 API 汇总，切换窗口
    response = admin_client.get("/api/v1/dashboard/summary", params={"days": 30})
    # Then：>100 全量统计（分级之和等于总数），跨窗口不变
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_current"] > 100
    levels = {item["level"]: item["count"] for item in payload["level_counts"]}
    assert set(levels) == {"P1", "P2", "P3", "P4"}
    assert sum(levels.values()) == payload["total_current"]
    assert payload["supplier_total"] >= 125 and payload["active_supplier_total"] >= 100
    distribution = {item["code"]: item["count"] for item in payload["source_distribution"]}
    assert distribution["hardening-evidence-source"] >= 100
    assert sum(distribution.values()) >= payload["total_current"]
    for days in (7, 90):
        other = admin_client.get("/api/v1/dashboard/summary", params={"days": days}).json()
        assert other["total_current"] == payload["total_current"]
        assert {item["level"]: item["count"] for item in other["level_counts"]} == levels


def test_supplier_with_history_rejects_delete_and_keeps_history(admin_client: TestClient) -> None:
    # Given：有风险关联历史的供应商（含现行与失效提醒）
    supplier_id = _supplier_id(DELETE_SUPPLIER_CODE)
    with SessionLocal() as session:
        legal_name = session.scalar(select(Supplier.legal_name).where(Supplier.id == supplier_id))
        current_alert_id = session.scalar(select(RiskAlert.id).where(
            RiskAlert.match_id.in_(select(SupplierEventMatch.id).where(
                SupplierEventMatch.supplier_id == supplier_id)),
            current_alert_condition(NOW)))
    assert current_alert_id is not None
    impact = admin_client.get(f"/api/v1/suppliers/{supplier_id}/deletion-impact").json()
    assert impact["can_delete"] is False
    assert impact["blocked_reason"] == "supplier_has_risk_history"
    assert impact["match_count"] >= 1 and impact["alert_count"] >= 2
    # When：真实 DELETE
    response = admin_client.delete(f"/api/v1/suppliers/{supplier_id}")
    # Then：409 拒绝且历史详情仍可访问，影响统计不变
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_has_risk_history"
    detail = admin_client.get(f"/api/v1/risk-alerts/{current_alert_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["supplier_name"] == legal_name
    after = admin_client.get(f"/api/v1/suppliers/{supplier_id}/deletion-impact").json()
    assert (after["match_count"], after["alert_count"]) == (
        impact["match_count"], impact["alert_count"])


def test_lossless_edit_keeps_paused_state_and_child_rows(admin_client: TestClient) -> None:
    # Given：暂停供应商（完整子表资料）
    supplier_id = _supplier_id(EDIT_SUPPLIER_CODE)
    read = admin_client.get(f"/api/v1/suppliers/{supplier_id}")
    assert read.status_code == 200, read.text
    before = read.json()
    assert before["enabled"] is False
    payload = {key: before[key] for key in EDIT_FIELDS}
    payload.update(industry="hardening-edited", expected_updated_at=before["updated_at"])
    # When：无损 PUT 编辑
    edited = admin_client.put(f"/api/v1/suppliers/{supplier_id}", json=payload)
    # Then：仍暂停且子表行 id 不变；陈旧版本被准确拒绝
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["enabled"] is False and body["industry"] == "hardening-edited"
    for table in ("aliases", "sites", "products"):
        assert [item["id"] for item in body[table]] == [item["id"] for item in before[table]]
    paused = admin_client.get("/api/v1/suppliers", params={"enabled": False, "limit": 100}).json()
    assert any(item["id"] == supplier_id for item in paused["items"])
    stale = {**payload, "expected_updated_at": "2020-01-01T00:00:00Z"}
    conflict = admin_client.put(f"/api/v1/suppliers/{supplier_id}", json=stale)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "supplier_changed"


def test_shared_evidence_cleanup_is_safe(receipt: HardeningSeedReceipt) -> None:
    # Given：seed 的共享证据 + 一条已过保留窗口的独立探针链（真实提交）
    tag = uuid4().hex[:10]
    old = NOW - timedelta(days=91)
    with SessionLocal.begin() as session:
        supplier = Supplier(
            supplier_code=f"HRD-CLN-{tag}", legal_name=f"清理探针{tag}", country_code="CN")
        session.add(supplier)
        session.flush()
        signal = RawSignal(
            source_id=SOURCE_ID, external_id=f"cln-{tag}", title=f"清理探针信号{tag}",
            content=f"清理探针{tag}", published_at=old, collected_at=old,
            fingerprint=f"hardening-cln-{tag}", raw_data={})
        session.add(signal)
        session.flush()
        event = RiskEvent(
            dedup_key=f"hrd-cln-{tag}", event_type="compliance", severity="high",
            summary=f"清理探针事件{tag}", start_at=old, end_at=old, confidence=0.9, facts={})
        session.add(event)
        session.flush()
        session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
        match = SupplierEventMatch(
            supplier_id=supplier.id, event_id=event.id, match_type="legal_name", score=70,
            reasons=["清理探针"], evidence=[])
        session.add(match)
        session.flush()
        session.add(RiskAlert(
            match_id=match.id, level="P3", score=60, score_detail={}, status="expired",
            expires_at=old, expiry_kind="finite", updated_at=old, created_at=old))
        probe_match_id, probe_event_id = match.id, event.id
    # When：真实清理（90 天保留）
    with SessionLocal.begin() as session:
        result = cleanup_retention(
            session, RetentionSettings(signal_days=90, event_days=90, run_days=30),
            now_utc=datetime.now(UTC))
    # Then：探针链被清理；共享证据与保留期内证据不受误删
    assert result.expired_alerts >= 1
    with SessionLocal() as session:
        assert _count(session, RiskAlert, RiskAlert.match_id == probe_match_id) == 0
        assert session.get(RiskEvent, probe_event_id) is None
        shared = session.scalar(
            select(RiskEvent).where(RiskEvent.dedup_key == SHARED_EVENT_DEDUP_KEY))
        assert shared is not None
        matches = list(session.scalars(
            select(SupplierEventMatch.id).where(SupplierEventMatch.event_id == shared.id)))
        assert len(matches) == 2
        assert _count(session, RiskAlert, RiskAlert.match_id.in_(matches)) == 2
        assert _count(session, RiskEventSignal, RiskEventSignal.event_id == shared.id) == 1
        expired = _count(session, RiskAlert, RiskAlert.status == "expired")
        assert expired >= receipt["expired_alert_total"]


def test_monitoring_health_reports_stale_heartbeat_as_degraded(admin_client: TestClient) -> None:
    # Given：无运行时观测 → unknown（可调度信源存在，不误报 inactive）
    baseline = admin_client.get("/api/v1/system/monitoring-health")
    assert baseline.status_code == 200, baseline.text
    body = baseline.json()
    assert body["overall"] == "unknown" and body["scheduler"]["status"] == "unknown"
    assert any(item["state"] not in {"disabled", "on_demand"} for item in body["sources"])
    # When：提交一条过期心跳
    stale_at = datetime.now(UTC) - timedelta(seconds=181)
    with SessionLocal.begin() as session:
        session.add(SchedulerRuntimeState(
            job_key="scheduler", status="succeeded", heartbeat_at=stale_at,
            last_success_at=stale_at))
    try:
        response = admin_client.get("/api/v1/system/monitoring-health")
    finally:
        with SessionLocal.begin() as session:
            session.execute(delete(SchedulerRuntimeState).where(
                SchedulerRuntimeState.job_key == "scheduler"))
    # Then：overall 降级且 scheduler 段给出 stale 证据与稳定字段
    assert response.status_code == 200, response.text
    degraded = response.json()
    assert degraded["overall"] == "degraded"
    assert degraded["scheduler"]["status"] == "stale"
    assert (degraded["scheduler"]["age_seconds"] or 0) >= 181
    for item in degraded["sources"]:
        assert {"source_id", "state", "reason_code", "next_expected_at"} <= set(item)


def test_notification_delivery_links_resolve_to_real_alerts(admin_client: TestClient) -> None:
    # Given：seed 用纯渲染函数写入的投递内容（固定 frontend url）
    with SessionLocal() as session:
        rows = list(session.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.content.contains(f"{FRONTEND_BASE_URL}/risks/"))
            .order_by(NotificationDelivery.id)))
    assert len(rows) >= 5
    groups: dict[str, list[NotificationDelivery]] = {}
    for row in rows:
        groups.setdefault(row.title or "", []).append(row)
    digest = max(groups.values(), key=len)
    assert len(digest) == 2 and {row.status for row in digest} == {"success", "merged"}
    # When：真实 API 发现投递记录（alert id 来自 API，不硬编码）
    listing = admin_client.get("/api/v1/notifications/deliveries", params={"limit": 200})
    assert listing.status_code == 200, listing.text
    api_rows = {item["id"]: item for item in listing.json()["items"]}
    # Then：每行链接可定位真实提醒，且内容含可审计供应商标识
    for row in rows:
        item = api_rows[row.id]
        assert item["alert_id"] == row.alert_id
        assert f"{FRONTEND_BASE_URL}/risks/{row.alert_id}" in (row.content or "")
        assert item["status"] == row.status and item["pushed_level"] == row.pushed_level
        assert item["delivered_at"] is not None
        detail = admin_client.get(f"/api/v1/risk-alerts/{row.alert_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["supplier_name"] in (row.title or "") + (row.content or "")
        assert f"/risks/{row.alert_id}" in (row.content or "")


def test_unauthenticated_and_viewer_failures_are_accurate(receipt: HardeningSeedReceipt) -> None:
    # 未登录：全部管理/汇总接口 401，且不泄漏汇总数据
    paths = ("/api/v1/dashboard/summary", "/api/v1/notifications/deliveries",
             "/api/v1/system/monitoring-health")
    with _api_client(None) as anon_client:
        responses = {path: anon_client.get(path) for path in paths}
    assert all(response.status_code == 401 for response in responses.values())
    assert "total_current" not in responses["/api/v1/dashboard/summary"].text
    # viewer：写权限与管理端 403，读总览 200
    with _api_client(VIEWER_USERNAME) as viewer:
        supplier_id = _supplier_id(DELETE_SUPPLIER_CODE)
        assert viewer.delete(f"/api/v1/suppliers/{supplier_id}").status_code == 403
        assert viewer.put(f"/api/v1/suppliers/{supplier_id}", json={}).status_code == 403
        assert viewer.get("/api/v1/notifications/deliveries").status_code == 403
        assert viewer.get("/api/v1/dashboard/summary").status_code == 200
