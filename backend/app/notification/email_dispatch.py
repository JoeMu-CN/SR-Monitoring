"""按用户邮件投递：级别/供应商过滤、去重、即时发送与分组合并摘要。

与固定 4 渠道完全独立：邮件记录带 user_id，去重键为
``(alert_id, 'email', user_id)``；固定渠道仍只处理 user_id IS NULL 的记录，
因此本模块的读写都不会改变钉钉/飞书/Server酱/PushPlus 的既有行为。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import NotificationSettings
from app.notification import delivery_queries as queries
from app.notification.email_provider import EmailProvider
from app.notification.models import NotificationDelivery
from app.notification.providers import NotificationError
from app.notification.rendering import (
    DigestMember,
    alert_context,
    render_alert_payload,
    render_digest,
)
from app.notification.user_targets import EmailTarget, load_targets
from app.risks.models import RiskAlert, SupplierEventMatch

logger = logging.getLogger("notification")

EMAIL_CHANNEL = "email"


def email_transport_ready(
    settings: NotificationSettings, provider_names: set[str]
) -> bool:
    """邮件分支开关：显式注入 email 渠道（测试）或环境变量已完整配置。

    ``build_providers()`` 只构建固定 4 渠道，生产环境邮件实例按用户逐个创建，
    因此这里用与 ``EmailProvider.enabled`` 相同的三项配置判断，避免分支静默失效。
    """
    if EMAIL_CHANNEL in provider_names:
        return True
    return bool(
        settings.email_enabled and settings.email_smtp_host and settings.email_from
    )


def supplier_id_for_alert(session: Session, alert: RiskAlert) -> int | None:
    """alert → match → supplier_id；归属缺失时返回 None。"""
    if alert.match_id is None:
        return None
    match = session.get(SupplierEventMatch, alert.match_id)
    return match.supplier_id if match is not None else None


def dispatch_email(
    session: Session,
    settings: NotificationSettings,
    alert: RiskAlert,
    target: EmailTarget,
    quiet: dict[str, object] | None,
    now: datetime,
    summary: dict[str, int],
) -> None:
    """按单个收件人展开一条提醒：级别/供应商过滤 → 去重 → 即时或入队。"""
    if alert.level not in target.levels:
        return
    supplier_id = supplier_id_for_alert(session, alert)
    if target.supplier_ids is not None and supplier_id not in target.supplier_ids:
        return

    existing = queries.get_delivery(
        session, alert.id, EMAIL_CHANNEL, user_id=target.user_id
    )
    if existing is not None:
        action = queries.plan_redelivery(existing, alert.level)
        if action is None:
            return
        summary[action] += 1
    elif queries.is_backfill(alert, now):
        # 与固定渠道一致：首次为用户开启邮件时，不补发基线窗口（24h）之前
        # 的历史提醒，否则管理员刚勾选就会收到一批旧风险。
        summary["backfill_pending"] += 1
        return

    supplier_name, event_type, event_summary, reasons = alert_context(session, alert)
    title, content = render_alert_payload(
        alert,
        supplier_name,
        event_type,
        event_summary,
        reasons,
        settings.frontend_url,
    )

    delivery = existing
    if delivery is None:
        delivery = NotificationDelivery(
            alert_id=alert.id,
            channel=EMAIL_CHANNEL,
            user_id=target.user_id,
            status="queued",
            title=title,
            content=content[:2000],
            pushed_level=alert.level,
        )
        session.add(delivery)
        session.flush()
    else:
        delivery.pushed_level = alert.level
        delivery.title = title
        delivery.content = content[:2000]

    provider = EmailProvider(settings, recipient=target.email)
    action = queries.classify_dispatch(
        session,
        provider,
        alert,
        quiet,
        now,
        settings,
        user_id=target.user_id,
        email_limits=(
            settings.email_user_hourly_limit,
            settings.email_global_hourly_limit,
        ),
    )
    if action == "immediate":
        action = _send_now(session, provider, delivery, title, content, settings, now)
    else:
        # 限频 P1 转合并队列；quiet_suppressed 等状态留痕
        delivery.status = "queued" if action == "rate_limited" else action
    summary[action] += 1


def _send_now(
    session: Session,
    provider: EmailProvider,
    delivery: NotificationDelivery,
    title: str,
    content: str,
    settings: NotificationSettings,
    now: datetime,
) -> str:
    """复用 service 的单条发送/重试语义；局部导入避免模块循环依赖。"""
    from app.notification.service import send_with_retry

    return send_with_retry(session, provider, delivery, title, content, settings, now=now)


def _over_limit(
    session: Session, settings: NotificationSettings, user_id: int, now: datetime
) -> bool:
    """邮件限频：单用户额度或全局额度任一超限即视为超限。"""
    return (
        queries.hourly_sent_count(session, EMAIL_CHANNEL, now, user_id=user_id)
        >= settings.email_user_hourly_limit
        or queries.hourly_sent_total(session, EMAIL_CHANNEL, now)
        >= settings.email_global_hourly_limit
    )


def _valid_members(
    session: Session,
    batch: list[NotificationDelivery],
    now: datetime,
    summary: dict[str, int],
) -> tuple[list[NotificationDelivery], list[DigestMember]]:
    """发送前复查提醒有效性；到期成员留痕并排除出摘要。"""
    current_batch: list[NotificationDelivery] = []
    members: list[DigestMember] = []
    for delivery in batch:
        if delivery.alert_id is None:
            continue
        alert = queries.get_current_alert(session, delivery.alert_id, now)
        if alert is None:
            delivery.status = "expired_suppressed"
            delivery.error = "alert_no_longer_current_or_valid"
            summary["expired_suppressed"] += 1
            continue
        current_batch.append(delivery)
        members.append(queries.digest_member(session, alert))
    return current_batch, members


def process_email_merge_queue(
    session: Session,
    settings: NotificationSettings,
    quiet: dict[str, object] | None,
    now: datetime,
    summary: dict[str, int],
) -> None:
    """按用户分组处理 queued 邮件：免打扰/限频抑制、有效性复查、摘要发送。"""
    queued = list(
        session.scalars(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status == "queued",
                NotificationDelivery.channel == EMAIL_CHANNEL,
                NotificationDelivery.user_id.is_not(None),
            )
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        )
    )
    if not queued:
        return
    targets = {target.user_id: target for target in load_targets(session)}
    grouped: dict[int, list[NotificationDelivery]] = {}
    for delivery in queued:
        if delivery.user_id is not None:
            grouped.setdefault(delivery.user_id, []).append(delivery)

    for user_id, rows in grouped.items():
        target = targets.get(user_id)
        if target is None:
            # 方案已撤销、邮箱非法或账号停用：保留记录留待人工排查，不发送。
            logger.info("邮件合并队列跳过 user_id=%s：当前无有效推送方案", user_id)
            continue
        batch = [row for row in rows if queries.merge_due(row, settings, now)]
        if not batch:
            continue
        # 免打扰：组内全为 P2 且处于免打扰时段 → 全部抑制留痕（P1 始终可达）
        if queries.in_quiet_hours(now, quiet) and all(
            (row.pushed_level or "P2") == "P2" for row in batch
        ):
            for row in batch:
                row.status = "quiet_suppressed"
            summary["quiet_suppressed"] += len(batch)
            continue
        # 限频：兜底时长内超限则抑制；有兜底到期则强制发送（防积压）
        if _over_limit(session, settings, user_id, now) and all(
            now - row.created_at < timedelta(minutes=queries.QUEUED_FORCE_SEND_MINUTES)
            for row in batch
        ):
            for row in batch:
                row.status = "rate_limited"
            summary["rate_limited"] += len(batch)
            continue
        current_batch, members = _valid_members(session, batch, now, summary)
        if not current_batch:
            continue
        title, content = render_digest(members, settings.frontend_url)
        provider = EmailProvider(settings, recipient=target.email)
        try:
            provider.send(title, content)
        except NotificationError as exc:
            queries.mark_batch_failure(
                current_batch, exc, settings.retry_attempts, now, summary
            )
            continue
        queries.mark_batch_success(current_batch, title, content, now, summary)
