"""投递记录查询、去重判定与批量投递状态标记。

从 service.py 拆出（每生产文件 ≤250 纯 LOC）：逐 (alert_id, channel) 的
查询、重推判定、合并批次就绪/抑制判定、成员资料查询与批量成功/失败
状态标记；纯编排流程留在 service.py。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import NotificationSettings
from app.notification.models import NotificationDelivery, NotificationSubscription
from app.notification.providers import NotifyProvider
from app.notification.rendering import DigestMember
from app.notification.rendering import alert_context as _alert_context
from app.risks.models import RiskAlert
from app.risks.query_validity import current_alert_condition

LEVEL_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
# 合并队列兜底：超过该时长强制发送（即使限频），防止无限积压
QUEUED_FORCE_SEND_MINUTES = 120
# 切换基线：无投递记录且创建早于该窗口的候选按历史漏投报告，不自动补发
BACKFILL_GRACE_MINUTES = 24 * 60

# 语义：消息已实际到达渠道（摘要成员 merged 同样算送达，同级不重发）
DELIVERED_STATUSES = frozenset({"success", "merged"})
# 可恢复状态：条件解除后复查（current 有效性/免打扰/限频）再重新投递
RECOVERABLE_STATUSES = frozenset(
    {"quiet_suppressed", "rate_limited", "expired_suppressed"}
)


def get_delivery(
    session: Session, alert_id: int, channel: str
) -> NotificationDelivery | None:
    """按 (alert_id, channel) 精确取投递记录（唯一去重判据，无全局游标）。"""
    return session.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.alert_id == alert_id,
            NotificationDelivery.channel == channel,
        )
    )


def count_legacy_unlinked(session: Session) -> int:
    """legacy 记录计数（alert_id IS NULL：旧版摘要首条等无归属记录），仅报告。"""
    return int(
        session.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.alert_id.is_(None)
            )
        )
        or 0
    )


def global_subscription(
    session: Session, settings: NotificationSettings
) -> NotificationSubscription:
    """全局订阅配置（推送级别与免打扰），缺失时按默认值创建。"""
    row = session.scalar(
        select(NotificationSubscription).where(
            NotificationSubscription.channel == "global"
        )
    )
    if row is None:
        row = NotificationSubscription(
            channel="global",
            receiver="全局配置",
            push_levels=list(settings.push_levels),
            enabled=True,
        )
        session.add(row)
        session.flush()
    return row


def active_channels(session: Session, provider_names: set[str]) -> list[str]:
    """返回当前应推送的渠道名（订阅启用 ∩ 可用 Provider）。

    订阅表无任何渠道记录时按可用 Provider 推送（默认行为）。
    """
    rows = list(
        session.scalars(
            select(NotificationSubscription).where(
                NotificationSubscription.channel != "global"
            )
        )
    )
    if not rows:
        return sorted(provider_names)
    enabled = {row.channel for row in rows if row.enabled}
    return sorted(name for name in provider_names if name in enabled)


def in_quiet_hours(now: datetime, quiet_hours: dict[str, object] | None) -> bool:
    """免打扰时段判定（支持跨天时段，如 22:00–08:00）。"""
    if not quiet_hours:
        return False
    try:
        start = str(quiet_hours.get("start", ""))
        end = str(quiet_hours.get("end", ""))
        if not start or not end or start == end:
            return False
        current = now.time().strftime("%H:%M")
        if start < end:
            return start <= current < end
        return current >= start or current < end  # 跨天时段
    except (TypeError, ValueError):
        return False


def hourly_sent_count(session: Session, channel: str, now: datetime) -> int:
    """单渠道近一小时已成功投递的消息数（摘要按消息数计 1）。"""
    return int(
        session.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.channel == channel,
                NotificationDelivery.status == "success",
                NotificationDelivery.delivered_at >= now - timedelta(hours=1),
            )
        )
        or 0
    )


def plan_redelivery(existing: NotificationDelivery, alert_level: str) -> str | None:
    """已存在投递记录时的重推决策；None 表示本轮跳过，其余值即 summary 键。"""
    if existing.status == "failed":
        return None  # 终态：不再重试
    if existing.status == "queued":
        return None  # 由合并队列统一处理
    if existing.status in DELIVERED_STATUSES:
        pushed = existing.pushed_level
        if pushed is None:
            return None
        if LEVEL_ORDER.get(alert_level, 99) < LEVEL_ORDER.get(pushed, 99):
            return "upgraded"
        return None  # 同级或降级不重发
    if existing.status in RECOVERABLE_STATUSES:
        return "recovered"
    return None


def is_backfill(alert: RiskAlert, now: datetime) -> bool:
    """切换基线判定：创建早于回填窗口的候选按历史漏投报告（不自动补发）。"""
    return alert.created_at < now - timedelta(minutes=BACKFILL_GRACE_MINUTES)


def classify_dispatch(
    session: Session,
    provider: NotifyProvider,
    alert: RiskAlert,
    quiet: dict[str, object] | None,
    now: datetime,
    settings: NotificationSettings,
) -> str:
    """投递前判定：返回 immediate / queued / quiet_suppressed / rate_limited。"""
    if alert.level == "P2" and in_quiet_hours(now, quiet):
        return "quiet_suppressed"  # 免打扰仅抑制 P2（P1 始终可达）
    if alert.level != "P1":
        return "queued"
    if hourly_sent_count(session, provider.name, now) >= settings.hourly_limit:
        return "rate_limited"  # 单渠道超限转合并队列
    return "immediate"


def merge_due(
    delivery: NotificationDelivery, settings: NotificationSettings, now: datetime
) -> bool:
    """合并队列就绪判定：首发看窗口；重试按 attempt 线性退避（锚定 updated_at）。"""
    if delivery.attempt <= 0:
        return now - delivery.created_at >= timedelta(
            minutes=settings.merge_window_minutes
        )
    return now - delivery.updated_at >= timedelta(
        minutes=settings.merge_window_minutes * delivery.attempt
    )


def get_current_alert(
    session: Session, alert_id: int, now: datetime
) -> RiskAlert | None:
    """取仍在有效期内的提醒（发送前复查 current 有效性），行锁防并发。"""
    return session.scalar(
        select(RiskAlert)
        .where(RiskAlert.id == alert_id, current_alert_condition(now))
        .with_for_update()
    )


def prepare_merge_batch(
    session: Session,
    queued: list[NotificationDelivery],
    channel: str,
    settings: NotificationSettings,
    quiet: dict[str, object] | None,
    now: datetime,
    summary: dict[str, int],
) -> list[NotificationDelivery]:
    """选出到期批次；免打扰/限频抑制时标记留痕并返回空列表。"""
    batch = [
        d
        for d in queued
        if d.channel == channel and merge_due(d, settings, now)
    ]
    if not batch:
        return batch
    # 全部为 P2 且处于免打扰时段 → 抑制留痕（条件解除后恢复）
    if in_quiet_hours(now, quiet) and all(
        (d.pushed_level or "P2") == "P2" for d in batch
    ):
        for d in batch:
            d.status = "quiet_suppressed"
        summary["quiet_suppressed"] += len(batch)
        return []
    # 限频：兜底时长内超限则抑制；有兜底到期则强制发送（防积压）
    if hourly_sent_count(session, channel, now) >= settings.hourly_limit:
        if all(
            now - d.created_at < timedelta(minutes=QUEUED_FORCE_SEND_MINUTES)
            for d in batch
        ):
            for d in batch:
                d.status = "rate_limited"
            summary["rate_limited"] += len(batch)
            return []
    return batch


def digest_member(session: Session, alert: RiskAlert) -> DigestMember:
    """构造摘要成员（供应商标识 + 自身 alert 归属）。"""
    supplier_name, event_type, _summary, _reasons = _alert_context(session, alert)
    return DigestMember(
        level=alert.level,
        supplier_name=supplier_name,
        event_type=event_type,
        alert_id=alert.id,
    )


def mark_batch_success(
    members: list[NotificationDelivery],
    title: str,
    content: str,
    now: datetime,
    summary: dict[str, int],
) -> None:
    """摘要成功：首条 success、其余 merged；全部保留 alert_id/channel，
    统一 delivered_at/title/content；pushed_level 保持成员自身等级。"""
    for idx, delivery in enumerate(members):
        delivery.status = "success" if idx == 0 else "merged"
        delivery.delivered_at = now
        delivery.title = title
        delivery.content = content[:2000]
        delivery.error = None
    summary["merged"] += len(members)


def mark_batch_failure(
    members: list[NotificationDelivery],
    exc: Exception,
    retry_attempts: int,
    now: datetime,
    summary: dict[str, int],
) -> None:
    """摘要失败：全批一致 attempt 与退避，达上限全批 failed（不改 created_at）。"""
    next_attempt = max(delivery.attempt for delivery in members) + 1
    terminal = next_attempt >= retry_attempts
    for delivery in members:
        delivery.attempt = next_attempt
        delivery.error = str(exc)[:500]
        delivery.status = "failed" if terminal else "queued"
        if not terminal:
            # 退避锚点：显式写本轮失败时刻（服务端 onupdate 同 Session 内不可回读）
            delivery.updated_at = now
    if terminal:
        summary["failed"] += len(members)
    else:
        summary["queued"] += len(members)
