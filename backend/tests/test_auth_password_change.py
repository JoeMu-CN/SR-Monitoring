"""本人改密与旧密码路由边界测试。"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import hash_password, verify_password

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


def test_self_password_change_accepts_correct_old_password(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    user = auth_as("viewer", "self-correct")
    user.password_hash = hash_password("OldSecret1")
    db_session.commit()

    # When
    response = client.post(
        f"/api/v1/auth/users/{user.id}/password",
        json={"old_password": "OldSecret1", "new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 200
    assert response.json() == {"detail": "密码已更新"}
    assert verify_password("NewSecret2", user.password_hash)


def test_self_password_change_rejects_wrong_old_password(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    user = auth_as("viewer", "self-wrong")
    user.password_hash = hash_password("OldSecret1")
    db_session.commit()

    # When
    response = client.post(
        f"/api/v1/auth/users/{user.id}/password",
        json={"old_password": "WrongSecret1", "new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 400
    assert response.json() == {"detail": "原密码错误"}
    assert verify_password("OldSecret1", user.password_hash)


@pytest.mark.parametrize("new_password", ["password1", f"{'密' * 24}1"])
def test_self_password_change_rejects_weak_password_with_400(
    new_password: str,
    client: TestClient,
    db_session: Session,
    auth_as: AuthAs,
) -> None:
    # Given
    user = auth_as("viewer", f"self-weak-{len(new_password)}")
    user.password_hash = hash_password("OldSecret1")
    db_session.commit()

    # When
    response = client.post(
        f"/api/v1/auth/users/{user.id}/password",
        json={"old_password": "OldSecret1", "new_password": new_password},
    )

    # Then
    assert response.status_code == 400
    assert verify_password("OldSecret1", user.password_hash)


def test_old_password_route_rejects_admin_targeting_another_user(
    client: TestClient, db_session: Session, auth_as: AuthAs
) -> None:
    # Given
    target = _make_user(db_session, "old-route-target", "OldSecret1")
    auth_as("platform_admin", "old-route-admin")

    # When
    response = client.post(
        f"/api/v1/auth/users/{target.id}/password",
        json={"old_password": "placeholder", "new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 403


def test_old_password_route_does_not_disclose_missing_non_self_target(
    client: TestClient, auth_as: AuthAs
) -> None:
    # Given
    auth_as("platform_admin", "non-disclosure-admin")

    # When
    response = client.post(
        "/api/v1/auth/users/999999999/password",
        json={"old_password": "placeholder", "new_password": "NewSecret2"},
    )

    # Then
    assert response.status_code == 403
