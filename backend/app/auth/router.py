"""认证路由：登录、当前用户、退出与用户管理。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.auth import schemas
from app.auth.models import AuthSession, User
from app.auth.security import (
    CurrentUser,
    SessionDependency,
    clear_login_failures,
    create_session,
    csrf_token_for_session,
    login_is_rate_limited,
    login_rate_limit_key,
    mask_ip,
    mask_user_agent,
    record_login_failure,
    revoke_session,
    role_permissions,
    session_token_hash,
    verify_csrf,
    verify_origin,
    verify_password,
    write_audit,
)
from app.auth.users_router import router as users_router
from app.config import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, SESSION_SECURE_COOKIE

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

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


router.include_router(users_router)
