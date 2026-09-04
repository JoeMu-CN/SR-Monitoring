"""用户管理端点契约测试：列表、创建、更新、权限与边界。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, User
from app.auth.security import create_session, hash_password, session_token_hash

AuthAs = Callable[[str, str], User]


def _make_user(
    db_session: Session,
    username: str,
    password: str,
    *,
    role: str = "viewer",
    status: str = "active",
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role=role,
        status=status,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _session_revoked(db_session: Session, token_hash: str) -> bool:
    record = db_session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    return record is not None and record.revoked_at is not None


def test_list_users_unauthenticated_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 401


def test_list_users_forbidden_for_viewer(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("viewer", "viewer-list-caller")
    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 403


def test_list_users_forbidden_for_risk_analyst(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("risk_analyst", "analyst-list-caller")
    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 403


def test_list_users_forbidden_for_risk_admin(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("risk_admin", "risk-admin-list-caller")
    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 403


def test_list_users_ordered_by_id_and_no_password_leak(
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-list-checker")
    user_a = _make_user(db_session, "list-alpha", "Secret001")
    user_b = _make_user(db_session, "list-beta", "Secret002")
    resp = client.get("/api/v1/auth/users")
    assert resp.status_code == 200
    users = resp.json()
    ids = [u["id"] for u in users]
    assert ids == sorted(ids)
    assert {user_a.id, user_b.id}.issubset(set(ids))
    assert all("password_hash" not in u for u in users)


def test_create_user_success(client: TestClient, db_session: Session, auth_as: AuthAs) -> None:
    auth_as("platform_admin", "admin-create")
    resp = client.post(
        "/api/v1/auth/users",
        json={
            "username": "new-analyst",
            "password": "Secret001",
            "role": "risk_analyst",
            "display_name": "New Analyst",
            "email": "analyst@example.com",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "new-analyst"
    assert body["role"] == "risk_analyst"
    assert body["display_name"] == "New Analyst"
    assert body["email"] == "analyst@example.com"
    assert body["status"] == "active"
    assert "password_hash" not in body


def test_create_user_duplicate_username_returns_409(
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-dup")
    _make_user(db_session, "dup-user", "Secret001")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "dup-user", "password": "Secret002"},
    )
    assert resp.status_code == 409


def test_create_user_weak_password_returns_400(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-weak-create")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "weak-create", "password": "password"},
    )
    assert resp.status_code == 400


def test_create_user_requires_csrf(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-csrf-create")
    client.headers.pop("X-CSRF-Token")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "csrf-create", "password": "Secret001"},
    )
    assert resp.status_code == 403


def test_create_user_requires_user_manage_permission(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("viewer", "viewer-create")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "should-fail", "password": "Secret001"},
    )
    assert resp.status_code == 403


def test_patch_user_updates_display_name_and_email(
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-patch")
    target = _make_user(db_session, "patch-target", "Secret001")
    resp = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"display_name": "Updated Name", "email": "new@example.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Updated Name"
    assert body["email"] == "new@example.com"
    assert body["role"] == "viewer"


def test_patch_user_role_change_revokes_sessions(
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-role-patch")
    target = _make_user(db_session, "role-target", "Secret001")
    token = create_session(db_session, user=target)
    old_hash = session_token_hash(token)
    resp = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"role": "risk_analyst"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "risk_analyst"
    assert _session_revoked(db_session, old_hash)


def test_patch_user_status_change_revokes_sessions(
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-status-patch")
    target = _make_user(db_session, "status-target", "Secret001")
    token = create_session(db_session, user=target)
    old_hash = session_token_hash(token)
    resp = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"status": "disabled"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    assert _session_revoked(db_session, old_hash)


def test_patch_nonexistent_user_returns_404(
    client: TestClient,
    auth_as: AuthAs,
) -> None:
    auth_as("platform_admin", "admin-404-patch")
    resp = client.patch(
        "/api/v1/auth/users/999999999",
        json={"display_name": "Ghost"},
    )
    assert resp.status_code == 404


def test_patch_user_requires_csrf(client: TestClient, db_session: Session, auth_as: AuthAs) -> None:
    auth_as("platform_admin", "admin-csrf-patch")
    target = _make_user(db_session, "csrf-patch-target", "Secret001")
    client.headers.pop("X-CSRF-Token")
    resp = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"display_name": "No CSRF"},
    )
    assert resp.status_code == 403


def test_patch_user_requires_user_manage_permission(
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    target = _make_user(db_session, "perm-patch-target", "Secret001")
    auth_as("risk_analyst", "analyst-patch")
    resp = client.patch(
        f"/api/v1/auth/users/{target.id}",
        json={"display_name": "Should Fail"},
    )
    assert resp.status_code == 403


def test_admin_cannot_downgrade_own_role(client: TestClient, auth_as: AuthAs) -> None:
    admin = auth_as("platform_admin", "self-role-admin")
    resp = client.patch(f"/api/v1/auth/users/{admin.id}", json={"role": "viewer"})
    assert resp.status_code == 403
    assert "角色" in resp.json()["detail"]


def test_admin_cannot_disable_own_account(client: TestClient, auth_as: AuthAs) -> None:
    admin = auth_as("platform_admin", "self-status-admin")
    resp = client.patch(f"/api/v1/auth/users/{admin.id}", json={"status": "disabled"})
    assert resp.status_code == 403
    assert "状态" in resp.json()["detail"]


def test_admin_self_profile_update_allowed(client: TestClient, auth_as: AuthAs) -> None:
    admin = auth_as("platform_admin", "self-profile-admin")
    resp = client.patch(
        f"/api/v1/auth/users/{admin.id}",
        json={"display_name": "New Name", "email": "new@example.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "New Name"
    assert body["email"] == "new@example.com"
    assert body["role"] == "platform_admin"


def test_admin_self_same_role_payload_allowed(client: TestClient, auth_as: AuthAs) -> None:
    admin = auth_as("platform_admin", "self-same-role")
    resp = client.patch(f"/api/v1/auth/users/{admin.id}", json={"role": "platform_admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "platform_admin"


def test_admin_self_same_status_payload_allowed(client: TestClient, auth_as: AuthAs) -> None:
    admin = auth_as("platform_admin", "self-same-status")
    resp = client.patch(f"/api/v1/auth/users/{admin.id}", json={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
