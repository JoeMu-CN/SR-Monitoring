"""认证、会话、权限与审计的核心实现。

设计要点：
- 密码使用 bcrypt 哈希，永不明文存储或返回。
- 会话为服务端不透明随机令牌，令牌原文仅通过 HttpOnly Cookie 下发，
  数据库只保存 HMAC-SHA256(SESSION_SECRET, token) 哈希。
- 所有授权以当前会话加载的服务端用户 / 角色为准，不信任任何浏览器提交的
  角色或用户 ID；``X-User-Role`` / ``X-User-Id`` 不参与授权。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Annotated

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, SecurityAuditEvent, User
from app.config import (
    ALLOWED_ORIGINS,
    BOOTSTRAP_ADMIN_PASSWORD,
    BOOTSTRAP_ADMIN_USERNAME,
    CSRF_COOKIE_NAME,
    RESEARCH_TRACK_ENABLED,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SESSION_COOKIE_NAME,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SESSION_SECRET,
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
# 权限矩阵（角色权限累加：低级角色包含高级角色的全部权限）
# ---------------------------------------------------------------------------
PERM_RISK_VIEW = "risk_view"
PERM_SUPPLIER_VIEW = "supplier_view"
PERM_SOURCE_STATUS_VIEW = "source_status_view"
PERM_RULE_SUMMARY_VIEW = "rule_summary_view"
PERM_RISK_QUERY_USE = "risk_query_use"
PERM_EXTERNAL_VERIFICATION = "external_verification"
PERM_REPORT_EXPORT = "report_export"
PERM_SUPPLIER_MANAGE = "supplier_manage"
PERM_SIGNAL_IMPORT = "signal_import"
PERM_ANALYSIS_RUN = "analysis_run"
PERM_SOURCE_MANAGE = "source_manage"
PERM_COLLECTION_TRIGGER = "collection_trigger"
PERM_SOURCE_AGENT_USE = "source_agent_use"
PERM_RULE_MANAGE = "rule_manage"
PERM_BUSINESS_AUDIT_VIEW = "business_audit_view"
PERM_USER_MANAGE = "user_manage"
PERM_SESSION_REVOKE = "session_revoke"
PERM_SECURITY_AUDIT_VIEW = "security_audit_view"
PERM_AUTH_CONFIG_MANAGE = "auth_config_manage"
PERM_RESEARCH_TASK_CREATE = "research_task_create"
PERM_RESEARCH_SCHEDULE_MANAGE = "research_schedule_manage"
PERM_RESEARCH_CLAIM_PROMOTE = "research_claim_promote"
PERM_RESEARCH_PROVIDER_MANAGE = "research_provider_manage"

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
    },
    "risk_analyst": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
        PERM_RISK_QUERY_USE,
        PERM_EXTERNAL_VERIFICATION,
        PERM_REPORT_EXPORT,
        PERM_RESEARCH_TASK_CREATE,
    },
    "risk_admin": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
        PERM_RISK_QUERY_USE,
        PERM_EXTERNAL_VERIFICATION,
        PERM_REPORT_EXPORT,
        PERM_RESEARCH_TASK_CREATE,
        PERM_RESEARCH_SCHEDULE_MANAGE,
        PERM_RESEARCH_CLAIM_PROMOTE,
        PERM_SUPPLIER_MANAGE,
        PERM_SIGNAL_IMPORT,
        PERM_ANALYSIS_RUN,
        PERM_SOURCE_MANAGE,
        PERM_COLLECTION_TRIGGER,
        PERM_SOURCE_AGENT_USE,
        PERM_RULE_MANAGE,
        PERM_BUSINESS_AUDIT_VIEW,
    },
    "platform_admin": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
        PERM_RISK_QUERY_USE,
        PERM_EXTERNAL_VERIFICATION,
        PERM_REPORT_EXPORT,
        PERM_SUPPLIER_MANAGE,
        PERM_SIGNAL_IMPORT,
        PERM_ANALYSIS_RUN,
        PERM_SOURCE_MANAGE,
        PERM_COLLECTION_TRIGGER,
        PERM_SOURCE_AGENT_USE,
        PERM_RULE_MANAGE,
        PERM_BUSINESS_AUDIT_VIEW,
        PERM_USER_MANAGE,
        PERM_SESSION_REVOKE,
        PERM_SECURITY_AUDIT_VIEW,
        PERM_AUTH_CONFIG_MANAGE,
        PERM_RESEARCH_TASK_CREATE,
        PERM_RESEARCH_SCHEDULE_MANAGE,
        PERM_RESEARCH_CLAIM_PROMOTE,
        PERM_RESEARCH_PROVIDER_MANAGE,
    },
}


def role_permissions(role: str) -> list[str]:
    permissions = set(ROLE_PERMISSIONS.get(role, set()))
    if not RESEARCH_TRACK_ENABLED:
        permissions.difference_update(
            {
                PERM_RESEARCH_TASK_CREATE,
                PERM_RESEARCH_SCHEDULE_MANAGE,
                PERM_RESEARCH_CLAIM_PROMOTE,
                PERM_RESEARCH_PROVIDER_MANAGE,
            }
        )
    return sorted(permissions)


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------
_WEAK_PASSWORDS = {
    "password",
    "12345678",
    "abcdefgh",
    "qwerty12",
    "admin123",
    "letmein1",
    "welcome1",
    "password1",
    "123456789",
    "changeme1",
}


def hash_password(password: str) -> str:
    hashed: bytes = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bool(
            bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        )
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> None:
    """弱口令与基本强度校验；不满足时抛出 400。"""
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="密码长度至少 8 位"
        )
    if password.strip().lower() in _WEAK_PASSWORDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="密码过于常见，请更换"
        )
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码需同时包含字母和数字",
        )


# ---------------------------------------------------------------------------
# 会话令牌
# ---------------------------------------------------------------------------
def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_token_hash(token: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def csrf_token_for_session(token: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        f"csrf:{token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def mask_ip(ip: str | None) -> str | None:
    """IP 脱敏：IPv4 末段置 0，IPv6 取前 4 段后截断。"""
    if not ip:
        return None
    ip = ip.strip()
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    if ":" in ip:
        parts = ip.split(":")
        if len(parts) >= 4:
            return ":".join(parts[:4]) + "::"
    return "unknown"


def mask_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:16]


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
    session.commit()
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
