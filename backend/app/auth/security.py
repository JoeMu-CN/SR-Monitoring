"""认证、会话、权限与审计的核心实现。

设计要点：
- 密码使用 bcrypt 哈希，永不明文存储或返回。
- 会话为服务端不透明随机令牌，令牌原文仅通过 HttpOnly Cookie 下发，
  数据库只保存 HMAC-SHA256(SESSION_SECRET, token) 哈希。
- 所有授权以当前会话加载的服务端用户 / 角色为准，不信任任何浏览器提交的
  角色或用户 ID；``X-User-Role`` / ``X-User-Id`` 不参与授权。
"""

from __future__ import annotations

import hmac
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, SecurityAuditEvent, User
from app.auth.passwords import hash_password as hash_password
from app.auth.passwords import validate_password_strength as validate_password_strength
from app.auth.passwords import verify_password as verify_password
from app.auth.permissions import PERM_ANALYSIS_RUN as PERM_ANALYSIS_RUN
from app.auth.permissions import PERM_AUTH_CONFIG_MANAGE as PERM_AUTH_CONFIG_MANAGE
from app.auth.permissions import PERM_BUSINESS_AUDIT_VIEW as PERM_BUSINESS_AUDIT_VIEW
from app.auth.permissions import PERM_COLLECTION_TRIGGER as PERM_COLLECTION_TRIGGER
from app.auth.permissions import PERM_EXTERNAL_VERIFICATION as PERM_EXTERNAL_VERIFICATION
from app.auth.permissions import PERM_REPORT_EXPORT as PERM_REPORT_EXPORT
from app.auth.permissions import PERM_RESEARCH_CLAIM_PROMOTE as PERM_RESEARCH_CLAIM_PROMOTE
from app.auth.permissions import PERM_RESEARCH_PROVIDER_MANAGE as PERM_RESEARCH_PROVIDER_MANAGE
from app.auth.permissions import PERM_RESEARCH_SCHEDULE_MANAGE as PERM_RESEARCH_SCHEDULE_MANAGE
from app.auth.permissions import PERM_RESEARCH_TASK_CREATE as PERM_RESEARCH_TASK_CREATE
from app.auth.permissions import PERM_RISK_QUERY_USE as PERM_RISK_QUERY_USE
from app.auth.permissions import PERM_RISK_VIEW as PERM_RISK_VIEW
from app.auth.permissions import PERM_RULE_MANAGE as PERM_RULE_MANAGE
from app.auth.permissions import PERM_RULE_SUMMARY_VIEW as PERM_RULE_SUMMARY_VIEW
from app.auth.permissions import PERM_SECURITY_AUDIT_VIEW as PERM_SECURITY_AUDIT_VIEW
from app.auth.permissions import PERM_SESSION_REVOKE as PERM_SESSION_REVOKE
from app.auth.permissions import PERM_SIGNAL_IMPORT as PERM_SIGNAL_IMPORT
from app.auth.permissions import PERM_SOURCE_AGENT_USE as PERM_SOURCE_AGENT_USE
from app.auth.permissions import PERM_SOURCE_MANAGE as PERM_SOURCE_MANAGE
from app.auth.permissions import PERM_SOURCE_STATUS_VIEW as PERM_SOURCE_STATUS_VIEW
from app.auth.permissions import PERM_SUPPLIER_MANAGE as PERM_SUPPLIER_MANAGE
from app.auth.permissions import PERM_SUPPLIER_VIEW as PERM_SUPPLIER_VIEW
from app.auth.permissions import PERM_USER_MANAGE as PERM_USER_MANAGE
from app.auth.permissions import ROLE_PERMISSIONS as ROLE_PERMISSIONS
from app.auth.permissions import role_permissions as role_permissions
from app.auth.tokens import csrf_token_for_session as csrf_token_for_session
from app.auth.tokens import generate_session_token as generate_session_token
from app.auth.tokens import mask_ip as mask_ip
from app.auth.tokens import mask_user_agent as mask_user_agent
from app.auth.tokens import session_token_hash as session_token_hash
from app.config import (
    ALLOWED_ORIGINS,
    BOOTSTRAP_ADMIN_PASSWORD,
    BOOTSTRAP_ADMIN_USERNAME,
    CSRF_COOKIE_NAME,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TIMEOUT_MINUTES,
)
from app.database import get_session

# 会话超时（由配置分钟 / 小时换算为 timedelta）
SESSION_IDLE_DELTA = timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
SESSION_ABSOLUTE_DELTA = timedelta(hours=SESSION_ABSOLUTE_TIMEOUT_HOURS)
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_WINDOW_SECONDS = 300
# ponytail: 当前部署只有一个 Uvicorn 进程；扩展到多进程/多副本时改用共享 Redis 限流。
_login_failures: dict[str, deque[float]] = {}
_login_failure_lock = Lock()


# ---------------------------------------------------------------------------
# 认证依赖
# ---------------------------------------------------------------------------
SessionDependency = Annotated[Session, Depends(get_session)]


def get_current_user(
    session: SessionDependency,
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> User:
    """从会话 Cookie 解析当前用户；任何失效都返回 401。"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证，请先登录"
        )
    record = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == session_token_hash(token))
    )
    if record is None or record.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话无效或已撤销"
        )
    now = datetime.now(UTC)
    if record.expires_at < now or record.last_activity_at < now - SESSION_IDLE_DELTA:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已过期")
    user = record.user
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    # 续期空闲计时
    record.last_activity_at = now
    session.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission: str) -> Callable[[User], User]:
    """返回一个依赖：要求当前用户拥有指定权限，否则 403。"""

    def _dependency(user: CurrentUser) -> User:
        if permission not in ROLE_PERMISSIONS.get(user.role, set()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user

    return _dependency


# ---------------------------------------------------------------------------
# CSRF / 来源校验
# ---------------------------------------------------------------------------
async def verify_origin(request: Request) -> None:
    """写请求来源校验：显式配置 ALLOWED_ORIGINS 时按名单，否则要求同源。"""
    origin = request.headers.get("origin")
    if origin is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少请求来源")
    if ALLOWED_ORIGINS:
        if origin not in ALLOWED_ORIGINS:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="来源不被允许")
        return
    expected = str(request.base_url).rstrip("/")
    if origin != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="来源不被允许")


async def verify_csrf(request: Request) -> None:
    """认证写请求同时校验来源和由会话令牌派生的双提交 CSRF Token。"""
    await verify_origin(request)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get("x-csrf-token")
    if not session_token or not cookie_token or not header_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
    expected = csrf_token_for_session(session_token)
    if not hmac.compare_digest(cookie_token, expected) or not hmac.compare_digest(
        header_token, expected
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")


def login_rate_limit_key(request: Request, username: str) -> str:
    source = mask_ip(request.client.host if request.client else None) or "unknown"
    return f"{source}:{username.strip().casefold()}"


def login_is_rate_limited(key: str) -> bool:
    now = time.monotonic()
    cutoff = now - _LOGIN_FAILURE_WINDOW_SECONDS
    with _login_failure_lock:
        attempts = _login_failures.setdefault(key, deque())
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return len(attempts) >= _LOGIN_FAILURE_LIMIT


def record_login_failure(key: str) -> None:
    with _login_failure_lock:
        _login_failures.setdefault(key, deque()).append(time.monotonic())


def clear_login_failures(key: str) -> None:
    with _login_failure_lock:
        _login_failures.pop(key, None)


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------
def write_audit(
    session: Session,
    *,
    action: str,
    actor_user_id: int | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    success: bool = True,
    request_id: str | None = None,
    source_ip_masked: str | None = None,
    detail: str | None = None,
) -> None:
    """写入脱敏安全审计事件；禁止传入凭据、Token、Cookie 或完整声明。"""
    session.add(
        SecurityAuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
            request_id=request_id,
            source_ip_masked=source_ip_masked,
            detail=detail,
        )
    )


# ---------------------------------------------------------------------------
# 会话创建与撤销
# ---------------------------------------------------------------------------
def create_session(
    session: Session,
    *,
    user: User,
    request: Request | None = None,
    token: str | None = None,
) -> str:
    raw_token = token or generate_session_token()
    now = datetime.now(UTC)
    record = AuthSession(
        token_hash=session_token_hash(raw_token),
        user_id=user.id,
        created_at=now,
        last_activity_at=now,
        expires_at=now + SESSION_ABSOLUTE_DELTA,
        source_ip_masked=mask_ip(request.client.host if request and request.client else None),
        user_agent_masked=mask_user_agent(
            request.headers.get("user-agent") if request else None
        ),
    )
    session.add(record)
    session.commit()
    return raw_token


def revoke_session(session: Session, record: AuthSession) -> None:
    record.revoked_at = datetime.now(UTC)
    session.commit()


def revoke_user_sessions(session: Session, user_id: int) -> int:
    """撤销某用户全部未撤销会话，返回撤销数量。"""
    now = datetime.now(UTC)
    records = session.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
    ).all()
    count = 0
    for record in records:
        record.revoked_at = now
        count += 1
    return count


# ---------------------------------------------------------------------------
# 首位管理员引导（仅当库内无用户且配置了引导变量时）
# ---------------------------------------------------------------------------
def ensure_bootstrap_admin(session: Session) -> User | None:
    if not BOOTSTRAP_ADMIN_USERNAME or not BOOTSTRAP_ADMIN_PASSWORD:
        return None
    existing = session.scalar(select(User).limit(1))
    if existing is not None:
        return None
    validate_password_strength(BOOTSTRAP_ADMIN_PASSWORD)
    user = User(
        username=BOOTSTRAP_ADMIN_USERNAME,
        password_hash=hash_password(BOOTSTRAP_ADMIN_PASSWORD),
        display_name="平台管理员",
        role="platform_admin",
        status="active",
        password_changed_at=datetime.now(UTC),
    )
    session.add(user)
    session.commit()
    return user
