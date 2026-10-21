"""推送管理矩阵 API：查看全部用户推送方案、批量保存勾选。

权限沿用 ``PERM_USER_MANAGE``（仅 platform_admin，见文档 D3）：
- GET 返回全部用户（含 viewer 行，四列固定 false）；
- PUT 只更新提交行，viewer 与「无邮箱却勾选」一律 422；
- 每次保存写一次审计（不含邮箱等敏感字段）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import (
    PERM_USER_MANAGE,
    mask_ip,
    require_permission,
    verify_csrf,
    write_audit,
)
from app.database import get_session
from app.notification.push_matrix_schemas import (
    PushMatrixRow,
    PushMatrixUpdateRequest,
)
from app.notification.user_models import (
    UserNotificationSubscription,
    UserSupplierAssignment,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["通知管理"])

SessionDependency = Annotated[Session, Depends(get_session)]
PushAdmin = Annotated[User, Depends(require_permission(PERM_USER_MANAGE))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


def _supplier_counts(session: Session) -> dict[int, int]:
    """一次 group by 构造 user_id → 已分配供应商数。"""
    rows = session.execute(
        select(UserSupplierAssignment.user_id, func.count()).group_by(
            UserSupplierAssignment.user_id
        )
    )
    return {user_id: int(total) for user_id, total in rows}


def _matrix_rows(session: Session) -> list[PushMatrixRow]:
    """构造矩阵行：viewer 行照常返回，但四个开关一律 false（D2）。"""
    subscriptions = {
        row.user_id: row
        for row in session.scalars(select(UserNotificationSubscription))
    }
    counts = _supplier_counts(session)
    rows: list[PushMatrixRow] = []
    for user in session.scalars(select(User).order_by(User.id)):
        subscription = subscriptions.get(user.id)
        if subscription is None or user.role == "viewer":
            push_p1 = push_p2 = push_p3 = push_monthly = False
        else:
            push_p1 = subscription.push_p1
            push_p2 = subscription.push_p2
            push_p3 = subscription.push_p3
            push_monthly = subscription.push_monthly_report
        rows.append(
            PushMatrixRow(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                role=user.role,
                email=user.email,
                push_p1=push_p1,
                push_p2=push_p2,
                push_p3=push_p3,
                push_monthly_report=push_monthly,
                supplier_count=counts.get(user.id, 0),
            )
        )
    return rows


def _validate(session: Session, payload: PushMatrixUpdateRequest) -> dict[int, User]:
    """校验全部提交行后再落库：避免部分写入后报错。"""
    user_ids = list(dict.fromkeys(item.user_id for item in payload.items))
    users = {
        user.id: user
        for user in session.scalars(select(User).where(User.id.in_(user_ids)))
    }
    for item in payload.items:
        user = users.get(item.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"用户 {item.user_id} 不存在",
            )
        if user.role == "viewer":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="查看者不参与风险推送",
            )
        wants_push = (
            item.push_p1 or item.push_p2 or item.push_p3 or item.push_monthly_report
        )
        if wants_push and not (user.email or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"用户 {user.username} 未设置邮箱，无法开启推送",
            )
    return users


@router.get("/push-matrix", response_model=list[PushMatrixRow])
def get_push_matrix(
    session: SessionDependency,
    _: PushAdmin,
) -> list[PushMatrixRow]:
    """返回全部用户的推送矩阵（缺失方案行时四列均为 false）。"""
    return _matrix_rows(session)


@router.put("/push-matrix", response_model=list[PushMatrixRow])
def update_push_matrix(
    payload: PushMatrixUpdateRequest,
    session: SessionDependency,
    request: Request,
    admin: PushAdmin,
    _: CsrfGuard,
) -> list[PushMatrixRow]:
    """批量保存勾选：存在则改、不存在则建，返回保存后的完整矩阵。"""
    if not payload.items:
        return _matrix_rows(session)
    users = _validate(session, payload)
    user_ids = list(dict.fromkeys(item.user_id for item in payload.items))
    subscriptions = {
        row.user_id: row
        for row in session.scalars(
            select(UserNotificationSubscription).where(
                UserNotificationSubscription.user_id.in_(user_ids)
            )
        )
    }
    flags = {"p1": 0, "p2": 0, "p3": 0, "monthly": 0}
    for item in payload.items:
        row = subscriptions.get(item.user_id)
        if row is None:
            row = UserNotificationSubscription(user_id=item.user_id)
            session.add(row)
            subscriptions[item.user_id] = row
        row.push_p1 = item.push_p1
        row.push_p2 = item.push_p2
        row.push_p3 = item.push_p3
        row.push_monthly_report = item.push_monthly_report
        for key, enabled in (
            ("p1", item.push_p1),
            ("p2", item.push_p2),
            ("p3", item.push_p3),
            ("monthly", item.push_monthly_report),
        ):
            if enabled:
                flags[key] += 1
    session.flush()
    write_audit(
        session,
        action="push_matrix_update",
        actor_user_id=admin.id,
        resource_type="notification_push_matrix",
        source_ip_masked=mask_ip(request.client.host if request.client else None),
        detail=(
            f"users={len(users)};p1={flags['p1']};p2={flags['p2']};"
            f"p3={flags['p3']};monthly={flags['monthly']}"
        ),
    )
    session.commit()
    return _matrix_rows(session)
