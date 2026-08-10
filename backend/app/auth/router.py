"""认证路由：登录、当前用户、退出与用户管理。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.auth import schemas
from app.auth.models import AuthSession, User
from app.auth.security import (
    PERM_USER_MANAGE,
    CurrentUser,
    SessionDependency,
    clear_login_failures,
    create_session,
    csrf_token_for_session,
    hash_password,
    login_is_rate_limited,
    login_rate_limit_key,
    mask_ip,
    mask_user_agent,
    record_login_failure,
    require_permission,
    revoke_session,
    revoke_user_sessions,
    role_permissions,
    session_token_hash,
    validate_password_strength,
    verify_csrf,
    verify_origin,
    verify_password,
    write_audit,
)
from app.config import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, SESSION_SECURE_COOKIE

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

AdminUser = Annotated[User, Depends(require_permission(PERM_USER_MANAGE))]
OriginGuard = Annotated[None, Depends(verify_origin)]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


def _request_ip_ua(request: Request) -> tuple[str | None, str | None]:
    return (
        mask_ip(request.client.host if request.client else None),
        mask_user_agent(request.headers.get("user-agent")),
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=SESSION_SECURE_COOKIE,
        samesite="lax",
        path="/",
        max_age=None,  # 过期由服务端会话绝对时间控制
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token_for_session(token),
        httponly=False,
        secure=SESSION_SECURE_COOKIE,
        samesite="lax",
        path="/",
        max_age=None,
    )


@router.post("/login")
async def login(
    payload: schemas.LoginRequest,
    response: Response,
    session: SessionDependency,
    request: Request,
    _: OriginGuard,
) -> schemas.UserRead:
    rate_limit_key = login_rate_limit_key(request, payload.username)
    if login_is_rate_limited(rate_limit_key):
        ip, _user_agent = _request_ip_ua(request)
        write_audit(
            session,
            action="login_rate_limited",
            success=False,
            resource_type="user",
            resource_id=payload.username,
            source_ip_masked=ip,
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后再试",
        )
    user = session.scalar(
        select(User).where(User.username == payload.username).limit(1)
    )
    # 统一错误文案，不区分“用户不存在”与“密码错误”
    if user is None or not verify_password(payload.password, user.password_hash):
        record_login_failure(rate_limit_key)
        ip, _user_agent = _request_ip_ua(request)
        write_audit(
            session,
            action="login",
            success=False,
            resource_type="user",
            resource_id=payload.username,
            source_ip_masked=ip,
            detail="凭据校验失败",
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    if user.status != "active":
        ip, _user_agent = _request_ip_ua(request)
        write_audit(
            session,
            action="login",
            actor_user_id=user.id,
            success=False,
            resource_type="user",
            resource_id=str(user.id),
            source_ip_masked=ip,
            detail="账号不可用",
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号未启用")

    clear_login_failures(rate_limit_key)
    token = create_session(session, user=user, request=request)
    user.last_login_at = datetime.now(UTC)
    ip, _user_agent = _request_ip_ua(request)
    write_audit(
        session,
        action="login",
        actor_user_id=user.id,
        success=True,
        resource_type="user",
        resource_id=str(user.id),
        source_ip_masked=ip,
    )
    session.commit()
    _set_session_cookie(response, token)
    return schemas.UserRead.model_validate(user)


@router.get("/me")
def me(user: CurrentUser) -> schemas.MeResponse:
    return schemas.MeResponse(
        user=schemas.UserRead.model_validate(user),
        permissions=role_permissions(user.role),
    )


@router.post("/logout")
async def logout(
    response: Response,
    session: SessionDependency,
    request: Request,
    user: CurrentUser,
    _: CsrfGuard,
) -> dict[str, str]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        record = session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == session_token_hash(token)
            )
        )
        if record is not None and record.revoked_at is None:
            revoke_session(session, record)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    write_audit(session, action="logout", actor_user_id=user.id, success=True)
    session.commit()
    return {"detail": "已退出登录"}


@router.get("/users")
def list_users(session: SessionDependency, _: AdminUser) -> list[schemas.UserRead]:
    users = session.scalars(select(User).order_by(User.id)).all()
    return [schemas.UserRead.model_validate(u) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: schemas.UserCreate,
    session: SessionDependency,
    request: Request,
    admin: AdminUser,
    _: CsrfGuard,
) -> schemas.UserRead:
    if session.scalar(
        select(User).where(User.username == payload.username).limit(1)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    validate_password_strength(payload.password)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        email=payload.email,
        role=payload.role,
        status="active",
        password_changed_at=datetime.now(UTC),
    )
    session.add(user)
    session.flush()
    ip, _user_agent = _request_ip_ua(request)
    write_audit(
        session,
        action="user_create",
        actor_user_id=admin.id,
        success=True,
        resource_type="user",
        resource_id=str(user.id),
        source_ip_masked=ip,
        detail=f"role={user.role}",
    )
    session.commit()
    return schemas.UserRead.model_validate(user)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: schemas.UserUpdate,
    session: SessionDependency,
    request: Request,
    admin: AdminUser,
    _: CsrfGuard,
) -> schemas.UserRead:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    changed: list[str] = []
    if payload.role is not None and payload.role != user.role:
        user.role = payload.role
        changed.append("role")
    if payload.status is not None and payload.status != user.status:
        user.status = payload.status
        changed.append("status")
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email
    session.flush()

    # 角色或状态变更必须撤销其现有会话，防止越权持续
    if changed:
        revoked = revoke_user_sessions(session, user.id)
        ip, _user_agent = _request_ip_ua(request)
        write_audit(
            session,
            action="user_update",
            actor_user_id=admin.id,
            success=True,
            resource_type="user",
            resource_id=str(user.id),
            source_ip_masked=ip,
            detail=f"changed={','.join(changed)};revoked_sessions={revoked}",
        )
    session.commit()
    return schemas.UserRead.model_validate(user)


@router.post("/users/{user_id}/password")
async def set_password(
    user_id: int,
    payload: schemas.PasswordChange,
    session: SessionDependency,
    request: Request,
    current: CurrentUser,
    _: CsrfGuard,
) -> dict[str, str]:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    is_self = current.id == user.id
    if not is_self and PERM_USER_MANAGE not in role_permissions(current.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    if is_self and not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="原密码错误")

    validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(UTC)
    revoked = revoke_user_sessions(session, user.id)
    ip, _user_agent = _request_ip_ua(request)
    write_audit(
        session,
        action="password_change",
        actor_user_id=current.id,
        success=True,
        resource_type="user",
        resource_id=str(user.id),
        source_ip_masked=ip,
        detail=f"self={is_self};revoked_sessions={revoked}",
    )
    session.commit()
    return {"detail": "密码已更新"}
