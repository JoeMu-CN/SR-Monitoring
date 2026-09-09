"""供应商原子删除保护与审计的真实 PostgreSQL 集成测试。

验收点：
- GET /{id}/deletion-impact（supplier_manage）：全部 match 计入历史（不只 current），
  alert_count 含全部状态；sites/products/aliases 计数；blocked_reason=supplier_has_risk_history。
- DELETE：父行 FOR UPDATE + match 复查。有历史 → 409 且业务不变，拒绝审计在独立提交后可见；
  无历史 → 删除与 supplier_delete 成功审计同事务，resource_id 为字符串。
- PATCH enabled：supplier_paused / supplier_resumed 审计；失败不伪装成功。
- 真实双 Session 两种顺序：关联先提交 → DELETE 409（拒绝审计独立可见）；
  DELETE 先锁父行并提交 → 新关联不能引用已删除供应商（FK KEY SHARE 冲突）。
- impact/DELETE/PATCH 的权限与 CSRF 由现有安全层拒绝。
"""
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import SecurityAuditEvent
from app.database import SessionLocal
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct, SupplierSite

BLOCKED_REASON = "supplier_has_risk_history"


def add_supplier(db_session: Session, code: str, *, enabled: bool = True) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"{code} 供应商",
        country_code="CN",
        registry_no=None,
        enabled=enabled,
    )
    db_session.add(supplier)
    db_session.flush()
    supplier.sites = [
        SupplierSite(
            site_name="工厂甲",
            country_code="CN",
            region="浙江省",
            city="杭州市",
            district="滨江区",
            address="测试路1号",
            latitude=None,
            longitude=None,
        )
    ]
    supplier.products = [SupplierProduct(name="测试产品", keywords=["测试"])]
    supplier.aliases = [
        SupplierAlias(
            alias="测试别名",
            language="zh",
            normalized_alias="测试别名",
        )
    ]
    db_session.flush()
    return supplier


def add_history(
    db_session: Session,
    supplier: Supplier,
    *,
    alert_status: str | None = "current",
) -> SupplierEventMatch:
    """追加一条风险关联；alert_status=None 表示只建 match 不建提醒。"""
    event_row = RiskEvent(
        dedup_key=f"{supplier.supplier_code}-{uuid4().hex}",
        event_type="other",
        event_subtype=None,
        severity="medium",
        summary="删除保护测试事件",
        confidence=0.9,
        facts={},
    )
    db_session.add(event_row)
    db_session.flush()
    match_row = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event_row.id,
        match_type="legal_name",
        score=50,
        reasons=[],
        evidence=[],
    )
    db_session.add(match_row)
    db_session.flush()
    if alert_status is not None:
        db_session.add(
            RiskAlert(
                match_id=match_row.id,
                level="P3",
                score=50,
                score_detail={},
                status=alert_status,
                expiry_kind="legacy",
            )
        )
    return match_row


def supplier_audit(
    db_session: Session, action: str, supplier_id: int
) -> SecurityAuditEvent | None:
    return db_session.scalar(
        select(SecurityAuditEvent).where(
            SecurityAuditEvent.action == action,
            SecurityAuditEvent.resource_type == "supplier",
            SecurityAuditEvent.resource_id == str(supplier_id),
        )
    )


def seed_history_supplier(db_session: Session) -> Supplier:
    supplier = add_supplier(db_session, f"SUP-DEL-H-{uuid4().hex[:8]}")
    add_history(db_session, supplier, alert_status="current")
    add_history(db_session, supplier, alert_status="expired")
    add_history(db_session, supplier, alert_status=None)
    db_session.commit()
    return supplier


# ------------------------------------------------------------------- 基线与 RED


def test_baseline_delete_without_history_succeeds(
    client: TestClient, db_session: Session
) -> None:
    supplier = add_supplier(db_session, f"SUP-DEL-OK-{uuid4().hex[:8]}")
    db_session.commit()

    response = client.delete(f"/api/v1/suppliers/{supplier.id}")

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Supplier, supplier.id) is None


def test_deletion_impact_counts_all_matches_and_all_alert_statuses(
    client: TestClient, db_session: Session
) -> None:
    supplier = seed_history_supplier(db_session)

    response = client.get(f"/api/v1/suppliers/{supplier.id}/deletion-impact")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_delete"] is False
    assert body["blocked_reason"] == BLOCKED_REASON
    assert body["match_count"] == 3
    assert body["alert_count"] == 2
    assert body["sites_count"] == 1
    assert body["products_count"] == 1
    assert body["aliases_count"] == 1


def test_deletion_impact_clean_supplier_is_deletable(
    client: TestClient, db_session: Session
) -> None:
    supplier = add_supplier(db_session, f"SUP-DEL-CLEAN-{uuid4().hex[:8]}")
    db_session.commit()

    response = client.get(f"/api/v1/suppliers/{supplier.id}/deletion-impact")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_delete"] is True
    assert body["blocked_reason"] is None
    assert body["match_count"] == 0
    assert body["alert_count"] == 0


def test_delete_with_history_is_rejected_and_audited(
    client: TestClient, db_session: Session
) -> None:
    supplier = seed_history_supplier(db_session)

    response = client.delete(f"/api/v1/suppliers/{supplier.id}")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == BLOCKED_REASON
    # 业务数据不变
    db_session.expire_all()
    assert db_session.get(Supplier, supplier.id) is not None
    match_total = db_session.scalar(
        select(func.count())
        .select_from(SupplierEventMatch)
        .where(SupplierEventMatch.supplier_id == supplier.id)
    )
    assert match_total == 3
    # 拒绝审计必须可见（独立提交，不随 409 回滚）
    rejected = supplier_audit(db_session, "supplier_delete_rejected", supplier.id)
    assert rejected is not None
    assert rejected.success is False
    assert rejected.detail is not None and BLOCKED_REASON in rejected.detail


def test_delete_with_match_but_no_alert_is_rejected(
    client: TestClient, db_session: Session
) -> None:
    supplier = add_supplier(db_session, f"SUP-DEL-NOALERT-{uuid4().hex[:8]}")
    add_history(db_session, supplier, alert_status=None)
    db_session.commit()

    response = client.delete(f"/api/v1/suppliers/{supplier.id}")

    assert response.status_code == 409
    assert supplier_audit(db_session, "supplier_delete_rejected", supplier.id) is not None


def test_delete_rechecks_references_added_after_impact(
    client: TestClient, db_session: Session
) -> None:
    supplier = add_supplier(db_session, f"SUP-DEL-RECHECK-{uuid4().hex[:8]}")
    db_session.commit()

    impact = client.get(f"/api/v1/suppliers/{supplier.id}/deletion-impact")
    assert impact.json()["can_delete"] is True

    # impact 之后、DELETE 之前出现新关联：DELETE 必须重新检查并拒绝。
    add_history(db_session, supplier, alert_status="current")
    db_session.commit()

    response = client.delete(f"/api/v1/suppliers/{supplier.id}")
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(Supplier, supplier.id) is not None


def test_delete_without_history_writes_success_audit(
    client: TestClient, db_session: Session
) -> None:
    supplier = add_supplier(db_session, f"SUP-DEL-AUDIT-{uuid4().hex[:8]}")
    db_session.commit()
    supplier_id = supplier.id

    response = client.delete(f"/api/v1/suppliers/{supplier_id}")

    assert response.status_code == 204
    audit = supplier_audit(db_session, "supplier_delete", supplier_id)
    assert audit is not None
    assert audit.success is True
    assert audit.resource_id == str(supplier_id)
    assert isinstance(audit.resource_id, str)


def test_patch_enabled_writes_pause_and_resume_audit(
    client: TestClient, db_session: Session
) -> None:
    supplier = add_supplier(db_session, f"SUP-PAUSE-{uuid4().hex[:8]}")
    db_session.commit()

    paused = client.patch(
        f"/api/v1/suppliers/{supplier.id}/enabled", json={"enabled": False}
    )
    assert paused.status_code == 200
    pause_audit = supplier_audit(db_session, "supplier_paused", supplier.id)
    assert pause_audit is not None and pause_audit.success is True

    resumed = client.patch(
        f"/api/v1/suppliers/{supplier.id}/enabled", json={"enabled": True}
    )
    assert resumed.status_code == 200
    resume_audit = supplier_audit(db_session, "supplier_resumed", supplier.id)
    assert resume_audit is not None and resume_audit.success is True


def test_patch_enabled_failure_does_not_write_success_audit(
    client: TestClient, db_session: Session
) -> None:
    response = client.patch("/api/v1/suppliers/999999/enabled", json={"enabled": False})
    assert response.status_code == 404
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SecurityAuditEvent)
            .where(SecurityAuditEvent.resource_type == "supplier")
        )
        == 0
    )


# ------------------------------------------------------------- 权限与 CSRF


def test_deletion_impact_requires_supplier_manage(
    client: TestClient, db_session: Session, auth_as: Callable[[str, str], None]
) -> None:
    supplier = add_supplier(db_session, f"SUP-IMPACT-PERM-{uuid4().hex[:8]}")
    db_session.commit()

    anonymous = TestClient(client.app)
    assert anonymous.get(
        f"/api/v1/suppliers/{supplier.id}/deletion-impact"
    ).status_code == 401

    auth_as("viewer", "delete-impact-viewer")
    assert client.get(
        f"/api/v1/suppliers/{supplier.id}/deletion-impact"
    ).status_code == 403


def test_delete_requires_permission_csrf_and_audits_nothing(
    client: TestClient, db_session: Session, auth_as: Callable[[str, str], None]
) -> None:
    supplier = add_supplier(db_session, f"SUP-DEL-PERM-{uuid4().hex[:8]}")
    db_session.commit()

    auth_as("viewer", "delete-viewer")
    forbidden = client.delete(f"/api/v1/suppliers/{supplier.id}")
    assert forbidden.status_code == 403

    auth_as("platform_admin", "delete-admin-no-csrf")
    client.headers.pop("X-CSRF-Token", None)
    csrf_miss = client.delete(f"/api/v1/suppliers/{supplier.id}")
    assert csrf_miss.status_code == 403

    db_session.expire_all()
    assert db_session.get(Supplier, supplier.id) is not None
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.action.in_(
                    ["supplier_delete", "supplier_delete_rejected"]
                )
            )
        )
        == 0
    )


# ------------------------------------------------- 真实双 Session 并发顺序


def _seed_standalone_supplier() -> dict[str, Any]:
    code = f"SUP-DEL-CONC-{uuid4().hex[:10]}"
    with SessionLocal() as session:
        supplier = Supplier(
            supplier_code=code,
            legal_name="并发删除供应商",
            country_code="CN",
            registry_no=None,
            enabled=True,
        )
        session.add(supplier)
        session.flush()
        row: dict[str, Any] = {"id": supplier.id, "code": code}
        session.commit()
    return row


def _cleanup_standalone_supplier(code: str) -> None:
    with SessionLocal.begin() as session:
        supplier_id = session.scalar(
            select(Supplier.id).where(Supplier.supplier_code == code)
        )
        session.execute(
            delete(RiskAlert).where(
                RiskAlert.match_id.in_(
                    select(SupplierEventMatch.id).where(
                        SupplierEventMatch.supplier_id == supplier_id
                    )
                )
            )
        )
        session.execute(
            delete(SupplierEventMatch).where(
                SupplierEventMatch.supplier_id == supplier_id
            )
        )
        session.execute(delete(RiskEvent).where(RiskEvent.dedup_key.like(f"{code}-%")))
        session.execute(delete(Supplier).where(Supplier.supplier_code == code))


@pytest.fixture
def concurrent_supplier() -> Generator[dict[str, Any]]:
    row = _seed_standalone_supplier()
    try:
        yield row
    finally:
        _cleanup_standalone_supplier(row["code"])


def _insert_match_committed(
    session: Session, supplier_code: str, supplier_id: int
) -> None:
    event_row = RiskEvent(
        dedup_key=f"{supplier_code}-{uuid4().hex}",
        event_type="other",
        event_subtype=None,
        severity="medium",
        summary="并发删除测试事件",
        confidence=0.9,
        facts={},
    )
    session.add(event_row)
    session.flush()
    session.add(
        SupplierEventMatch(
            supplier_id=supplier_id,
            event_id=event_row.id,
            match_type="legal_name",
            score=50,
            reasons=[],
            evidence=[],
        )
    )
    session.flush()


def test_history_committed_before_delete_rejects(
    concurrent_supplier: dict[str, Any],
) -> None:
    from app.suppliers.deletion import delete_supplier_guarded

    supplier_id = concurrent_supplier["id"]
    supplier_code = concurrent_supplier["code"]
    # 顺序一：关联事务先提交。
    with SessionLocal.begin() as session:
        supplier = session.get(Supplier, supplier_id)
        assert supplier is not None
        _insert_match_committed(session, supplier_code, supplier_id)

    # 随后的受保护删除（独立真实 Session）：必须 409，且拒绝审计已独立提交可见。
    with pytest.raises(HTTPException) as exc_info:
        with SessionLocal() as session:
            delete_supplier_guarded(session, supplier_id, actor_user_id=None)

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict) and detail["code"] == BLOCKED_REASON

    with SessionLocal() as session:
        rejected = session.scalar(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "supplier_delete_rejected",
                SecurityAuditEvent.resource_id == str(supplier_id),
            )
        )
        assert rejected is not None and rejected.success is False
        assert session.get(Supplier, supplier_id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(SupplierEventMatch)
                .where(SupplierEventMatch.supplier_id == supplier_id)
            )
            == 1
        )


def test_delete_lock_prevents_late_reference_to_deleted_supplier(
    concurrent_supplier: dict[str, Any],
) -> None:
    from app.suppliers.deletion import (
        delete_supplier_guarded,
        lock_supplier_for_delete,
    )

    supplier_id = concurrent_supplier["id"]
    supplier_code = concurrent_supplier["code"]
    barrier = Barrier(2)
    outcomes: dict[str, str] = {}

    def delete_first() -> None:
        with SessionLocal() as session:
            locked = lock_supplier_for_delete(session, supplier_id)
            assert locked is not None
            barrier.wait(timeout=10)
            delete_supplier_guarded(session, supplier_id, actor_user_id=None)
        outcomes["delete"] = "committed"

    def insert_late_reference() -> None:
        try:
            with SessionLocal() as session:
                barrier.wait(timeout=10)
                _insert_match_committed(session, supplier_code, supplier_id)
                session.commit()
            outcomes["insert"] = "committed"
        except IntegrityError:
            outcomes["insert"] = "fk_violation"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(delete_first), executor.submit(insert_late_reference)]
        for future in futures:
            future.result()

    assert outcomes["delete"] == "committed"
    assert outcomes["insert"] == "fk_violation"
    with SessionLocal() as session:
        assert session.get(Supplier, supplier_id) is None
        deleted_audit = session.scalar(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "supplier_delete",
                SecurityAuditEvent.resource_id == str(supplier_id),
            )
        )
        assert deleted_audit is not None and deleted_audit.success is True
        assert (
            session.scalar(
                select(func.count())
                .select_from(SupplierEventMatch)
                .where(SupplierEventMatch.supplier_id == supplier_id)
            )
            == 0
        )
