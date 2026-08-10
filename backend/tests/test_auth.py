"""认证模块测试：登录、会话、权限、用户管理与会话撤销。"""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app import config
from app.auth import security
from app.auth.models import User
from app.auth.security import hash_password


def _make_user(db_session, username, password, role="viewer", status="active"):
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role=role,
        status=status,
        password_changed_at=datetime.now(UTC),
    )
    db_session.add(user)
    db_session.commit()
    return user


def _login(client, username: str, password: str):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": "http://testserver"},
    )
    csrf_token = response.cookies.get("srm_session_csrf")
    if csrf_token:
        client.headers["Origin"] = "http://testserver"
        client.headers["X-CSRF-Token"] = csrf_token
    return response


def test_login_success_and_sets_cookie(client, db_session):
    _make_user(db_session, "alice", "secret123", role="viewer")
    resp = _login(client, "alice", "secret123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "alice"
    assert "password_hash" not in body
    assert body["role"] == "viewer"
    assert "srm_session" in resp.cookies
    assert "srm_session_csrf" in resp.cookies


def test_login_wrong_password(client, db_session):
    _make_user(db_session, "alice", "secret123")
    resp = _login(client, "alice", "wrong")
    assert resp.status_code == 401


def test_login_unknown_user(client):
    resp = _login(client, "ghost", "x")
    assert resp.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_permissions(client, db_session):
    _make_user(db_session, "admin", "secret123", role="platform_admin")
    _login(client, "admin", "secret123")
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert "user_manage" in resp.json()["permissions"]


def test_logout_revokes_session(client, db_session):
    _make_user(db_session, "alice", "secret123")
    _login(client, "alice", "secret123")
    assert client.get("/api/v1/auth/me").status_code == 200
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_create_user_requires_admin(client, db_session):
    _make_user(db_session, "viewer1", "secret123", role="viewer")
    _login(client, "viewer1", "secret123")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "bob", "password": "secret123", "role": "viewer"},
    )
    assert resp.status_code == 403


def test_admin_creates_and_login(client, db_session):
    _make_user(db_session, "admin", "secret123", role="platform_admin")
    _login(client, "admin", "secret123")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "bob", "password": "secret123", "role": "risk_analyst"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "risk_analyst"
    client.post("/api/v1/auth/logout")
    login = _login(client, "bob", "secret123")
    assert login.status_code == 200


def test_role_change_revokes_existing_session(client, db_session):
    _make_user(db_session, "admin", "secret123", role="platform_admin")
    _login(client, "admin", "secret123")
    created = client.post(
        "/api/v1/auth/users",
        json={"username": "carol", "password": "secret123", "role": "viewer"},
    ).json()
    carol_id = created["id"]
    client.post("/api/v1/auth/logout")
    login = _login(client, "carol", "secret123")
    old_token = login.cookies.get("srm_session")
    assert (
        client.get("/api/v1/auth/me", cookies={"srm_session": old_token}).status_code
        == 200
    )
    # 管理员修改角色 -> 撤销 carol 现有会话
    client.post("/api/v1/auth/logout")
    _login(client, "admin", "secret123")
    assert (
        client.patch(
            f"/api/v1/auth/users/{carol_id}", json={"role": "risk_analyst"}
        ).status_code
        == 200
    )
    # 旧令牌失效
    assert (
        client.get("/api/v1/auth/me", cookies={"srm_session": old_token}).status_code
        == 401
    )


def test_create_weak_password_rejected(client, db_session):
    _make_user(db_session, "admin", "secret123", role="platform_admin")
    _login(client, "admin", "secret123")
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": "weak", "password": "password", "role": "viewer"},
    )
    assert resp.status_code == 400


def test_auth_endpoints_ignore_x_user_role_header(client):
    """认证接口不信任浏览器自报的 X-User-Role。"""
    resp = client.get("/api/v1/auth/users", headers={"X-User-Role": "admin"})
    assert resp.status_code == 401


def test_password_change_self(client, db_session):
    alice = _make_user(db_session, "alice", "secret123")
    _login(client, "alice", "secret123")
    resp = client.post(
        f"/api/v1/auth/users/{alice.id}/password",
        json={"old_password": "secret123", "new_password": "newsecret1"},
    )
    assert resp.status_code == 200
    client.post("/api/v1/auth/logout")
    assert (
        _login(client, "alice", "newsecret1").status_code
        == 200
    )


def test_login_requires_origin(client, db_session):
    _make_user(db_session, "origin-user", "secret123")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "origin-user", "password": "secret123"},
    )
    assert response.status_code == 403


def test_authenticated_write_requires_csrf_header(client, db_session):
    _make_user(db_session, "csrf-user", "secret123")
    _login(client, "csrf-user", "secret123")
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/v1/auth/logout").status_code == 403


def test_login_rate_limit(client):
    for _ in range(5):
        assert _login(client, "rate-limit-user", "wrong").status_code == 401
    assert _login(client, "rate-limit-user", "wrong").status_code == 429


def test_production_requires_explicit_session_secret(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "_SESSION_SECRET_ENV", "")
    monkeypatch.setattr(config, "SESSION_SECURE_COOKIE", True)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://risk.example.com"])
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        config.validate_auth_config()


def test_production_requires_separate_session_secret(monkeypatch):
    shared_secret = "shared-production-secret-1234567890"
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "_SESSION_SECRET_ENV", shared_secret)
    monkeypatch.setattr(config, "DATA_SOURCE_SECRET_KEY", shared_secret)
    monkeypatch.setattr(config, "SESSION_SECURE_COOKIE", True)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://risk.example.com"])
    with pytest.raises(RuntimeError, match="DATA_SOURCE_SECRET_KEY"):
        config.validate_auth_config()


def test_production_requires_data_source_secret(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "_SESSION_SECRET_ENV", "session-secret-1234567890-abcdefgh")
    monkeypatch.setattr(config, "DATA_SOURCE_SECRET_KEY", "")
    monkeypatch.setattr(config, "SESSION_SECURE_COOKIE", True)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://risk.example.com"])
    with pytest.raises(RuntimeError, match="DATA_SOURCE_SECRET_KEY"):
        config.validate_auth_config()


def test_bootstrap_admin_rejects_weak_password(db_session, monkeypatch):
    monkeypatch.setattr(security, "BOOTSTRAP_ADMIN_USERNAME", "bootstrap-admin")
    monkeypatch.setattr(security, "BOOTSTRAP_ADMIN_PASSWORD", "password")
    with pytest.raises(HTTPException, match="密码过于常见"):
        security.ensure_bootstrap_admin(db_session)
