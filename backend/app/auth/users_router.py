"""认证用户管理路由。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

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
from app.notification.user_models import UserSupplierAssignment
from app.suppliers.models import Supplier

router = APIRouter()

AdminUser = Annotated[User, Depends(require_permission(PERM_USER_MANAGE))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


def _supplier_counts(session: Session) -> dict[int, int]:
    """一次 group by 取全部用户的负责供应商数量，避免逐用户查询。"""
    rows = session.execute(
        select(UserSupplierAssignment.user_id, func.count()).group_by(
            UserSupplierAssignment.user_id
        )
    )
    return {user_id: int(total) for user_id, total in rows}


def _user_suppliers(session: Session, user_id: int) -> list[schemas.UserSupplierRead]:
    """按 supplier_code 排序返回该用户负责的供应商。"""
    rows = session.scalars(
        select(Supplier)
        .join(UserSupplierAssignment, UserSupplierAssignment.supplier_id == Supplier.id)
        .where(UserSupplierAssignment.user_id == user_id)
        .order_by(Supplier.supplier_code)
    )
    return [schemas.UserSupplierRead.model_validate(supplier) for supplier in rows]


@router.get("/users")
def list_users(session: SessionDependency, _: AdminUser) -> list[schemas.UserRead]:
    users = session.scalars(select(User).order_by(User.id)).all()
    counts = _supplier_counts(session)
    return [
        schemas.UserRead.model_validate(user).model_copy(
            update={"supplier_count": counts.get(user.id, 0)}
        )
        for user in users
    ]


@router.get("/users/{user_id}/suppliers")
def list_user_suppliers(
    user_id: int, session: SessionDependency, _: AdminUser
) -> list[schemas.UserSupplierRead]:
    """查看用户负责的供应商（risk_analyst 的推送范围数据源）。"""
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return _user_suppliers(session, user_id)


@router.put("/users/{user_id}/suppliers")
def update_user_suppliers(
    user_id: int,
    payload: schemas.UserSupplierUpdate,
    session: SessionDependency,
    request: Request,
    admin: AdminUser,
    _: CsrfGuard,
) -> list[schemas.UserSupplierRead]:
    """全量替换负责供应商；仅 risk_analyst 允许非空。"""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    wanted = list(dict.fromkeys(payload.supplier_ids))
    if wanted and user.role != "risk_analyst":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "仅风险分析员可分配负责供应商")
    if wanted:
        found = set(session.scalars(select(Supplier.id).where(Supplier.id.in_(wanted))))
        missing = [supplier_id for supplier_id in wanted if supplier_id not in found]
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"供应商不存在: {missing}"
            )
    session.execute(
        delete(UserSupplierAssignment).where(UserSupplierAssignment.user_id == user_id)
    )
    if wanted:
        session.execute(
            insert(UserSupplierAssignment),
            [{"user_id": user_id, "supplier_id": supplier_id} for supplier_id in wanted],
        )
    write_audit(
        session,
        action="user_suppliers_update",
        actor_user_id=admin.id,
        resource_id=str(user_id),
        source_ip_masked=mask_ip(request.client.host if request.client else None),
        detail=f"count={len(wanted)}",
    )
    session.commit()
    return _user_suppliers(session, user_id)


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

    if admin.id == user_id:
        self_identity_changes: list[str] = []
        if payload.role is not None and payload.role != user.role:
            self_identity_changes.append("角色")
        if payload.status is not None and payload.status != user.status:
            self_identity_changes.append("状态")
        if self_identity_changes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"不能修改本人的{'、'.join(self_identity_changes)}，以防自锁",
            )

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
