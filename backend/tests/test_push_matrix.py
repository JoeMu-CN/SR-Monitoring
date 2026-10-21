"""推送管理矩阵与负责供应商 API 测试（批次 2 / 9.1）。

校验：矩阵读写与 viewer 行、权限、无邮箱勾选、负责供应商全量替换与角色约束。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.notification.user_models import (
    UserNotificationSubscription,
    UserSupplierAssignment,
)
from app.suppliers.models import Supplier

_username_counter = 0


def make_user(
    session: Session,
    *,
    role: str = "risk_admin",
    email: str | None = "user@example.com",
) -> User:
    global _username_counter
    _username_counter += 1
    user = User(
        username=f"matrix-user-{_username_counter}",
        password_hash="not-used-by-test",
        display_name=f"矩阵用户{_username_counter}",
        email=email,
        role=role,
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def make_supplier(session: Session, code: str, name: str) -> Supplier:
    global _username_counter
    _username_counter += 1
    supplier = Supplier(
        supplier_code=f"{code}-{_username_counter:04d}",
        legal_name=name,
        country_code="CN",
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def row_of(rows: list[dict[str, object]], user_id: int) -> dict[str, object]:
    return next(row for row in rows if row["user_id"] == user_id)


def user_row(rows: list[dict[str, object]], user_id: int) -> dict[str, object]:
    return next(row for row in rows if row["id"] == user_id)


def test_get_matrix_returns_all_users_and_disables_viewer(
    client: TestClient, db_session: Session
) -> None:
    """GET 返回全部用户；viewer 行四列固定 false（即使库里有勾选记录）。"""
    viewer = make_user(db_session, role="viewer", email="viewer@example.com")
    analyst = make_user(db_session, role="risk_analyst", email="analyst@example.com")
    db_session.add(
        UserNotificationSubscription(
            user_id=viewer.id,
            push_p1=True,
            push_p2=True,
            push_p3=True,
            push_monthly_report=True,
        )
    )
    db_session.add(UserNotificationSubscription(user_id=analyst.id, push_p1=True))
    db_session.flush()

    response = client.get("/api/v1/notifications/push-matrix")

    assert response.status_code == 200
    rows = response.json()
    viewer_row = row_of(rows, viewer.id)
    assert viewer_row["role"] == "viewer"
    assert [
        viewer_row["push_p1"],
        viewer_row["push_p2"],
        viewer_row["push_p3"],
        viewer_row["push_monthly_report"],
    ] == [False, False, False, False]
    analyst_row = row_of(rows, analyst.id)
    assert analyst_row["push_p1"] is True
    assert analyst_row["push_p2"] is False
    assert analyst_row["supplier_count"] == 0


def test_put_matrix_persists_and_returns_rows(
    client: TestClient, db_session: Session
) -> None:
    """PUT 保存后 GET 反映新值，缺失行按提交内容新建。"""
    analyst = make_user(db_session, role="risk_analyst", email="analyst@example.com")

    response = client.put(
        "/api/v1/notifications/push-matrix",
        json={
            "items": [
                {
                    "user_id": analyst.id,
                    "push_p1": True,
                    "push_p2": True,
                    "push_p3": False,
                    "push_monthly_report": True,
                }
            ]
        },
    )

    assert response.status_code == 200
    saved = row_of(response.json(), analyst.id)
    assert [
        saved["push_p1"],
        saved["push_p2"],
        saved["push_p3"],
        saved["push_monthly_report"],
    ] == [True, True, False, True]
    stored = db_session.scalar(
        select(UserNotificationSubscription).where(
            UserNotificationSubscription.user_id == analyst.id
        )
    )
    assert stored is not None
    assert stored.push_p1 is True
    assert stored.push_monthly_report is True

    fetched = row_of(client.get("/api/v1/notifications/push-matrix").json(), analyst.id)
    assert fetched["push_p1"] is True
    assert fetched["push_p3"] is False


def test_put_matrix_rejects_viewer(client: TestClient, db_session: Session) -> None:
    """提交 viewer 的行直接 422，防止绕过界面写入。"""
    viewer = make_user(db_session, role="viewer", email="viewer@example.com")

    response = client.put(
        "/api/v1/notifications/push-matrix",
        json={"items": [{"user_id": viewer.id, "push_p1": True}]},
    )

    assert response.status_code == 422
    assert "查看者" in response.json()["detail"]


def test_put_matrix_rejects_push_without_email(
    client: TestClient, db_session: Session
) -> None:
    """勾选任一开关但邮箱为空 → 422，detail 指明用户。"""
    analyst = make_user(db_session, role="risk_admin", email=None)

    response = client.put(
        "/api/v1/notifications/push-matrix",
        json={"items": [{"user_id": analyst.id, "push_p2": True}]},
    )

    assert response.status_code == 422
    assert analyst.username in response.json()["detail"]

    # 全部关闭则允许保存（清空勾选不需要邮箱）。
    allowed = client.put(
        "/api/v1/notifications/push-matrix",
        json={"items": [{"user_id": analyst.id}]},
    )
    assert allowed.status_code == 200


def test_push_matrix_requires_user_manage_permission(
    client: TestClient,
    db_session: Session,
    auth_as: Callable[[str, str], User],
) -> None:
    """非 platform_admin（无 PERM_USER_MANAGE）访问矩阵 → 403。"""
    del db_session
    auth_as("risk_admin", "matrix-risk-admin")

    assert client.get("/api/v1/notifications/push-matrix").status_code == 403
    response = client.put(
        "/api/v1/notifications/push-matrix", json={"items": []}
    )
    assert response.status_code == 403


def test_user_suppliers_roundtrip_sorted_by_code(
    client: TestClient, db_session: Session
) -> None:
    """负责供应商保存/回读：全量替换并按 supplier_code 排序。"""
    analyst = make_user(db_session, role="risk_analyst", email="analyst@example.com")
    first = make_supplier(db_session, "SUP-A", "甲供应商")
    second = make_supplier(db_session, "SUP-B", "乙供应商")

    response = client.put(
        f"/api/v1/auth/users/{analyst.id}/suppliers",
        json={"supplier_ids": [second.id, first.id]},
    )

    assert response.status_code == 200
    assert [row["supplier_code"] for row in response.json()] == [
        first.supplier_code,
        second.supplier_code,
    ]

    fetched = client.get(f"/api/v1/auth/users/{analyst.id}/suppliers")
    assert fetched.status_code == 200
    assert [row["legal_name"] for row in fetched.json()] == ["甲供应商", "乙供应商"]

    users = client.get("/api/v1/auth/users").json()
    assert user_row(users, analyst.id)["supplier_count"] == 2

    # 全量替换为空表示清空
    cleared = client.put(
        f"/api/v1/auth/users/{analyst.id}/suppliers", json={"supplier_ids": []}
    )
    assert cleared.status_code == 200
    assert cleared.json() == []
    assert db_session.scalars(select(UserSupplierAssignment)).all() == []


def test_user_suppliers_rejects_non_analyst(
    client: TestClient, db_session: Session
) -> None:
    """非 risk_analyst 传非空供应商列表 → 422；空列表允许。"""
    admin = make_user(db_session, role="risk_admin", email="admin@example.com")
    supplier = make_supplier(db_session, "SUP-C", "丙供应商")

    rejected = client.put(
        f"/api/v1/auth/users/{admin.id}/suppliers",
        json={"supplier_ids": [supplier.id]},
    )
    assert rejected.status_code == 422
    assert "风险分析员" in rejected.json()["detail"]

    empty = client.put(
        f"/api/v1/auth/users/{admin.id}/suppliers", json={"supplier_ids": []}
    )
    assert empty.status_code == 200


def test_user_suppliers_rejects_missing_supplier(
    client: TestClient, db_session: Session
) -> None:
    """不存在的 supplier_id → 422 并列出缺失 id。"""
    analyst = make_user(db_session, role="risk_analyst", email="analyst@example.com")

    response = client.put(
        f"/api/v1/auth/users/{analyst.id}/suppliers",
        json={"supplier_ids": [999_999_999]},
    )

    assert response.status_code == 422
    assert "999999999" in response.json()["detail"]


def test_user_suppliers_unknown_user_returns_404(
    client: TestClient, db_session: Session
) -> None:
    """用户不存在时 GET/PUT 均 404。"""
    del db_session
    assert client.get("/api/v1/auth/users/999999999/suppliers").status_code == 404
    assert (
        client.put(
            "/api/v1/auth/users/999999999/suppliers", json={"supplier_ids": []}
        ).status_code
        == 404
    )
