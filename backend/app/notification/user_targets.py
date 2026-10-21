"""邮件接收人判定：推送方案 + 供应商归属 → 收件人集合与候选级别并集。

规则来自《风险提醒邮件推送接入方案.md》7.1 / 7.2：
- 只处理 status=active 且非 viewer 的用户（viewer 即使库里有勾选记录也不投递，D2）；
- risk_analyst 的供应商范围取其 user_supplier_assignments，空集不降级为全量（D4）；
- 邮箱为空或格式非法直接跳过，日志只记 user_id 与原因，不落邮箱原文。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.notification.user_models import (
    UserNotificationSubscription,
    UserSupplierAssignment,
)

logger = logging.getLogger("notification")

#: 查看者不参与风险推送（文档 D2）。
SKIPPED_ROLE = "viewer"


@dataclass(frozen=True, slots=True)
class EmailTarget:
    """单个收件人的投递范围；supplier_ids 为 None 表示全量。"""

    user_id: int
    email: str
    levels: frozenset[str]
    supplier_ids: frozenset[int] | None


def _is_valid_email(email: str) -> bool:
    """轻量校验（与 email_provider 同口径）：单地址、无空白、无分隔符。"""
    if "\r" in email or "\n" in email:
        return False
    cleaned = email.strip()
    if (
        cleaned.count("@") != 1
        or any(character.isspace() for character in cleaned)
        or "," in cleaned
        or ";" in cleaned
    ):
        return False
    local_part, _, domain = cleaned.rpartition("@")
    return bool(local_part) and bool(domain)


def _levels_of(subscription: UserNotificationSubscription) -> frozenset[str]:
    """方案行 → 已勾选级别（P4 不提供开关）。"""
    levels: set[str] = set()
    if subscription.push_p1:
        levels.add("P1")
    if subscription.push_p2:
        levels.add("P2")
    if subscription.push_p3:
        levels.add("P3")
    return frozenset(levels)


def _assignments(session: Session) -> dict[int, set[int]]:
    """一条 SQL 全量加载归属后内存分组（供应商千级、分配万级无压力）。"""
    grouped: dict[int, set[int]] = {}
    rows = session.execute(
        select(UserSupplierAssignment.user_id, UserSupplierAssignment.supplier_id)
    )
    for user_id, supplier_id in rows:
        grouped.setdefault(user_id, set()).add(supplier_id)
    return grouped


def load_targets(session: Session) -> list[EmailTarget]:
    """加载本轮邮件接收人；无方案行即未订阅（内连接保证）。"""
    rows = session.execute(
        select(User, UserNotificationSubscription)
        .join(
            UserNotificationSubscription,
            UserNotificationSubscription.user_id == User.id,
        )
        .where(User.status == "active", User.role != SKIPPED_ROLE)
        .order_by(User.id)
    ).all()
    if not rows:
        return []
    assignments = _assignments(session)
    targets: list[EmailTarget] = []
    for user, subscription in rows:
        email = (user.email or "").strip()
        if not _is_valid_email(email):
            # 日志只保留 user_id 与原因，禁止写入邮箱原文。
            logger.warning("跳过邮件接收人 user_id=%s：邮箱缺失或格式非法", user.id)
            continue
        levels = _levels_of(subscription)
        if not levels:
            continue
        supplier_ids: frozenset[int] | None
        if user.role == "risk_analyst":
            owned = assignments.get(user.id) or set()
            if not owned:
                continue  # D4：空集不推送，避免每轮空转
            supplier_ids = frozenset(owned)
        else:
            supplier_ids = None  # risk_admin / platform_admin 按全量处理
        targets.append(
            EmailTarget(
                user_id=user.id,
                email=email,
                levels=levels,
                supplier_ids=supplier_ids,
            )
        )
    return targets


def subscribed_levels(session: Session) -> set[str]:
    """候选级别并集：active、非 viewer、有邮箱且有方案行的已勾选级别。

    供候选查询使用，只放宽查询范围不做邮箱格式校验；固定渠道仍按
    global.push_levels 处理，不会被用户勾选抬高范围。
    """
    rows = session.scalars(
        select(UserNotificationSubscription)
        .join(User, User.id == UserNotificationSubscription.user_id)
        .where(
            User.status == "active",
            User.role != SKIPPED_ROLE,
            User.email.is_not(None),
            func.btrim(User.email) != "",
        )
    )
    levels: set[str] = set()
    for subscription in rows:
        levels |= _levels_of(subscription)
    return levels
