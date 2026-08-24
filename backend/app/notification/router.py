"""通知管理 API：测试发送、投递记录、订阅配置。

权限：全部限 risk_admin / platform_admin（PERM_RULE_MANAGE）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import PERM_RULE_MANAGE, require_permission, verify_csrf
from app.config import get_notification_settings
from app.database import get_session
from app.notification import schemas
from app.notification.models import NotificationDelivery, NotificationSubscription
from app.notification.providers import NotificationError, build_providers
from app.notification.service import _global_subscription

router = APIRouter(prefix="/api/v1/notifications", tags=["通知管理"])

SessionDependency = Annotated[Session, Depends(get_session)]
NotifyAdmin = Annotated[User, Depends(require_permission(PERM_RULE_MANAGE))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]

DEFAULT_TEST_TITLE = "【测试】供应商风险监控平台通知链路"
DEFAULT_TEST_CONTENT = "这是一条测试消息：通知模块已接通，渠道配置正常。"


@router.post("/test", response_model=list[schemas.TestSendResult])
def send_test(
    payload: schemas.TestSendRequest | None = None,
    _: NotifyAdmin = None,
    __: CsrfGuard = None,
) -> list[schemas.TestSendResult]:
    """向所有已启用渠道发送测试消息，返回逐渠道实时结果。"""
    settings = get_notification_settings()
    title = (payload.title if payload else None) or DEFAULT_TEST_TITLE
    content = (payload.content if payload else None) or DEFAULT_TEST_CONTENT
    results: list[schemas.TestSendResult] = []
    providers = build_providers(settings)
    if not providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未启用任何通知渠道，请先在环境变量中配置渠道密钥与开关",
        )
    for provider in providers:
        try:
            provider.send(title, content, timeout=settings.timeout_seconds)
        except NotificationError as exc:
            results.append(
                schemas.TestSendResult(channel=provider.name, ok=False, detail=str(exc))
            )
        else:
            results.append(
                schemas.TestSendResult(channel=provider.name, ok=True, detail="发送成功")
            )
    return results


@router.get("/deliveries", response_model=schemas.DeliveryListResponse)
def list_deliveries(
    session: SessionDependency,
    _: NotifyAdmin = None,
    channel: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> schemas.DeliveryListResponse:
    """分页查询投递记录（渠道 / 状态过滤）。"""
    conditions = []
    if channel:
        conditions.append(NotificationDelivery.channel == channel)
    if status_filter:
        conditions.append(NotificationDelivery.status == status_filter)
    total = int(
        session.scalar(
            select(func.count(NotificationDelivery.id)).where(*conditions)
        )
        or 0
    )
    rows = session.scalars(
        select(NotificationDelivery)
        .where(*conditions)
        .order_by(NotificationDelivery.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return schemas.DeliveryListResponse(
        items=[
            schemas.DeliveryRead(
                id=row.id,
                alert_id=row.alert_id,
                channel=row.channel,
                status=row.status,
                title=row.title,
                pushed_level=row.pushed_level,
                attempt=row.attempt,
                error=row.error,
                delivered_at=row.delivered_at,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/subscriptions", response_model=list[schemas.SubscriptionRead])
def list_subscriptions(
    session: SessionDependency,
    _: NotifyAdmin = None,
) -> list[schemas.SubscriptionRead]:
    """查看当前订阅配置（全局级别/免打扰 + 各渠道启停）。"""
    _global_subscription(session, get_notification_settings())
    session.flush()
    rows = session.scalars(
        select(NotificationSubscription).order_by(
            NotificationSubscription.channel
        )
    ).all()
    return [
        schemas.SubscriptionRead(
            channel=row.channel,
            receiver=row.receiver,
            push_levels=row.push_levels,
            quiet_hours=row.quiet_hours,
            enabled=row.enabled,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.put("/subscriptions", response_model=schemas.SubscriptionRead)
def update_global_subscription(
    payload: schemas.SubscriptionUpsert,
    session: SessionDependency,
    _: NotifyAdmin = None,
    __: CsrfGuard = None,
) -> schemas.SubscriptionRead:
    """更新全局订阅配置（推送级别 / 免打扰时段）。"""
    if not payload.push_levels or any(
        level not in schemas.VALID_LEVELS for level in payload.push_levels
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"推送级别仅支持 {schemas.VALID_LEVELS}",
        )
    quiet = payload.quiet_hours
    if quiet is not None:
        start, end = str(quiet.get("start", "")), str(quiet.get("end", ""))
        if not start or not end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="免打扰时段需要 start 与 end（HH:MM）",
            )
    row = _global_subscription(session, get_notification_settings())
    row.push_levels = list(dict.fromkeys(payload.push_levels))
    row.quiet_hours = quiet
    session.commit()
    return schemas.SubscriptionRead(
        channel=row.channel,
        receiver=row.receiver,
        push_levels=row.push_levels,
        quiet_hours=row.quiet_hours,
        enabled=row.enabled,
        updated_at=row.updated_at,
    )


@router.put("/subscriptions/{channel}", response_model=schemas.SubscriptionRead)
def toggle_channel(
    channel: str,
    payload: schemas.ChannelToggle,
    session: SessionDependency,
    _: NotifyAdmin = None,
    __: CsrfGuard = None,
) -> schemas.SubscriptionRead:
    """启停单个渠道的推送（不删除记录，便于恢复）。"""
    if channel not in schemas.VALID_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"渠道仅支持 {schemas.VALID_CHANNELS}",
        )
    row = session.scalar(
        select(NotificationSubscription).where(
            NotificationSubscription.channel == channel
        )
    )
    if row is None:
        row = NotificationSubscription(
            channel=channel,
            receiver=payload.receiver,
            push_levels=["P1", "P2"],
            enabled=payload.enabled,
        )
        session.add(row)
    else:
        row.enabled = payload.enabled
        if payload.receiver is not None:
            row.receiver = payload.receiver
    session.commit()
    return schemas.SubscriptionRead(
        channel=row.channel,
        receiver=row.receiver,
        push_levels=row.push_levels,
        quiet_hours=row.quiet_hours,
        enabled=row.enabled,
        updated_at=row.updated_at,
    )
