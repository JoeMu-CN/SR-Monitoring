"""认证用户管理路由。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.auth import schemas
from app.auth.models import User
from app.auth.security import (
    PERM_USER_MANAGE,
    CurrentUser,
    SessionDependency,
    hash_password,
    mask_ip,
    require_permission,
    revoke_user_sessions,
    validate_password_strength,
    verify_csrf,
    verify_password,
    write_audit,
)

router = APIRouter()

AdminUser = Annotated[User, Depends(require_permission(PERM_USER_MANAGE))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


@router.get("/users")
def list_users(session: SessionDependency, _: AdminUser) -> list[schemas.UserRead]:
    users = session.scalars(select(User).order_by(User.id)).all()
    return [schemas.UserRead.model_validate(user) for user in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: schemas.UserCreate,
    session: SessionDependency,
    request: Request,
    admin: AdminUser,
    _: CsrfGuard,
) -> schemas.UserRead:
    if session.scalar(select(User).where(User.username == payload.username).limit(1)):
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
    write_audit(
        session,
        action="user_create",
        actor_user_id=admin.id,
        success=True,
        resource_type="user",
        resource_id=str(user.id),
        source_ip_masked=mask_ip(request.client.host if request.client else None),
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

    if changed:
        revoked = revoke_user_sessions(session, user.id)
        write_audit(
            session,
            action="user_update",
            actor_user_id=admin.id,
            success=True,
            resource_type="user",
            resource_id=str(user.id),
            source_ip_masked=mask_ip(request.client.host if request.client else None),
            detail=f"changed={','.join(changed)};revoked_sessions={revoked}",
        )
    session.commit()
    return schemas.UserRead.model_validate(user)


@router.post("/users/{user_id}/password")
async def change_password(
    user_id: int,
    payload: schemas.PasswordChange,
    session: SessionDependency,
    request: Request,
    current: CurrentUser,
    _: CsrfGuard,
) -> dict[str, str]:
    if current.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅可修改本人密码")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="原密码错误")

    validate_password_strength(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(UTC)
    revoked = revoke_user_sessions(session, user.id)
    write_audit(
        session,
        action="password_change",
        actor_user_id=current.id,
        success=True,
        resource_type="user",
        resource_id=str(user.id),
        source_ip_masked=mask_ip(request.client.host if request.client else None),
        detail=f"self=True;revoked_sessions={revoked}",
    )
    session.commit()
    return {"detail": "密码已更新"}


@router.post("/users/{user_id}/password-reset")
async def reset_password(
    user_id: int,
    payload: schemas.PasswordResetRequest,
    session: SessionDependency,
    request: Request,
    admin: AdminUser,
    _: CsrfGuard,
) -> dict[str, str]:
    if admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="请使用本人密码修改接口",
        )
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    validate_password_strength(
        payload.new_password,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(UTC)
    revoked = revoke_user_sessions(session, user.id)
    write_audit(
        session,
        action="password_reset",
        actor_user_id=admin.id,
        success=True,
        resource_type="user",
        resource_id=str(user.id),
        source_ip_masked=mask_ip(request.client.host if request.client else None),
        detail=f"revoked_sessions={revoked}",
    )
    session.commit()
    return {"detail": "密码已重置"}
