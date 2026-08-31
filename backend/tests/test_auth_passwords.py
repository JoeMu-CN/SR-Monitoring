"""本人改密与管理员密码重置契约测试。"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, SecurityAuditEvent, User
from app.auth.security import (
    create_session,
    hash_password,
    session_token_hash,
    verify_password,
)

AuthAs = Callable[[str, str], User]


def _make_user(
    db_session: Session,
    username: str,
    password: str,
    *,
    role: str = "viewer",
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role=role,
        status="active",
        password_changed_at=datetime.now(UTC),
    )
    db_session.add(user)
    db_session.commit()
    return user


def _login(client: TestClient, username: str, password: str) -> Response:
    response: Response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": "http://testserver"},
    )
    return response


def test_platform_admin_resets_another_user_without_old_password(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "reset-target", "OldSecret1")
    auth_as("platform_admin", "reset-admin")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 200
    assert response.json() == {"detail": "密码已重置"}
    assert verify_password("NewSecret2", target.password_hash)


def test_admin_reset_revokes_all_target_sessions(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "session-target", "OldSecret1")
    first_token = create_session(db_session, user=target)
    second_token = create_session(db_session, user=target)
    auth_as("platform_admin", "session-admin")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 200
    records = db_session.scalars(
        select(AuthSession).where(
            AuthSession.token_hash.in_(
                [session_token_hash(first_token), session_token_hash(second_token)]
            )
        )
    ).all()
    assert len(records) == 2
    assert all(record.revoked_at is not None for record in records)


def test_new_password_login_succeeds_after_admin_reset(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "new-login-target", "OldSecret1")
    auth_as("platform_admin", "new-login-admin")
    reset = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )
    assert reset.status_code == 200
    client.cookies.clear()

    # When
    response = _login(client, target.username, "NewSecret2")

    # Then
    assert response.status_code == 200


def test_old_password_login_fails_after_admin_reset(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "old-login-target", "OldSecret1")
    auth_as("platform_admin", "old-login-admin")
    reset = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )
    assert reset.status_code == 200
    client.cookies.clear()

    # When
    response = _login(client, target.username, "OldSecret1")

    # Then
    assert response.status_code == 401


def test_admin_session_remains_valid_after_resetting_another_user(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "admin-session-target", "OldSecret1")
    admin = auth_as("platform_admin", "persistent-admin")

    # When
    reset = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert reset.status_code == 200
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == admin.id


@pytest.mark.parametrize("role", ["viewer", "risk_analyst", "risk_admin"])
def test_roles_without_user_manage_cannot_reset_password(
    role: str, client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, f"{role}-target", "OldSecret1")
    auth_as(role, f"{role}-actor")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 403


def test_admin_reset_requires_csrf(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "csrf-reset-target", "OldSecret1")
    auth_as("platform_admin", "csrf-reset-admin")
    client.headers.pop("X-CSRF-Token")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 403


@pytest.mark.parametrize(
    "new_password",
    ["Short1", "password1", "LettersOnly", "1234567890", f"{'密' * 24}1"],
)
def test_admin_reset_rejects_weak_passwords_with_422(
    new_password: str,
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    # Given
    target = _make_user(db_session, f"weak-{new_password}", "OldSecret1")
    auth_as("platform_admin", f"weak-admin-{new_password}")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": new_password},
    )

    # Then
    assert response.status_code == 422


def test_admin_cannot_reset_self(
    client: TestClient, auth_as: AuthAs
) -> None:
    # Given
    admin = auth_as("platform_admin", "self-reset-admin")

    # When
    response = client.post(
        f"/api/v1/auth/users/{admin.id}/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 403
    assert "本人密码修改" in response.json()["detail"]


def test_authorized_admin_reset_missing_target_returns_404(
    client: TestClient, auth_as: AuthAs
) -> None:
    # Given
    auth_as("platform_admin", "missing-target-admin")

    # When
    response = client.post(
        "/api/v1/auth/users/999999999/password-reset",
        json={"new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 404


def test_admin_reset_forbids_old_password_extra_field(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "extra-field-target", "OldSecret1")
    auth_as("platform_admin", "extra-field-admin")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": "NewSecret2", "old_password": "OldSecret1"},
    )

    # Then
    assert response.status_code == 422


def test_admin_reset_writes_exact_masked_audit_without_secrets(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    old_password = "AuditOld1"
    new_password = "AuditNew2"
    target = _make_user(db_session, "audit-target", old_password)
    target_token = create_session(db_session, user=target)
    admin = auth_as("platform_admin", "audit-admin")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password-reset",
        json={"new_password": new_password},
        headers={"User-Agent": "secret-user-agent"},
    )

    # Then
    assert response.status_code == 200
    audit = db_session.scalar(
        select(SecurityAuditEvent).where(
            SecurityAuditEvent.action == "password_reset",
            SecurityAuditEvent.resource_id == str(target.id),
        )
    )
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.resource_type == "user"
    assert audit.success is True
    assert audit.source_ip_masked == "unknown"
    assert audit.detail == "revoked_sessions=1"
    audit_text = "|".join(
        value
        for value in (
            audit.action,
            audit.resource_type,
            audit.resource_id,
            audit.source_ip_masked,
            audit.detail,
        )
        if value is not None
    )
    for secret in (
        old_password,
        new_password,
        target.password_hash,
        target_token,
        client.cookies.get("srm_session_csrf") or "csrf-not-set",
        "secret-user-agent",
    ):
        assert secret not in audit_text
