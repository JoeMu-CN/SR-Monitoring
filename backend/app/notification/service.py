"""通知扫描编排：游标扫描 → 订阅过滤 → 防骚扰（合并/限频/免打扰）→ 发送。

设计（见《风险预警手机推送接入方案.md》第 6 章）：
- 零侵入：独立 notify_job 只读 risk_alerts，不改风险引擎。
- 新增推送：alert.id > 游标（投递记录中最大 alert_id）。
- 升级重推：同一 match 的 current alert 等级提高时重新推送。
- P1 即时单条；P2 入队等待合并窗口（15 分钟）后发合并摘要。
- 单渠道单小时限频（默认 20 条），超限转合并队列。
- 免打扰时段抑制 P2（P1 不受影响）。
- 发送失败按窗口退避重试，最终失败落 failed 记录（不静默丢失）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import NotificationSettings, get_notification_settings
from app.database import SessionLocal
from app.notification.models import NotificationDelivery, NotificationSubscription
from app.notification.providers import (
    NotificationError,
    NotifyProvider,
    build_providers,
)
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier

logger = logging.getLogger("notification")

LEVEL_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
# 合并队列兜底：超过该时长强制发送（即使限频），防止无限积压
QUEUED_FORCE_SEND_MINUTES = 120

SendFn = Callable[[str, str], None]

_notify_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 消息渲染
# ---------------------------------------------------------------------------
def _render_alert_payload(
    alert: RiskAlert,
    supplier_name: str,
    event_type: str,
    summary: str,
    reasons: list[str],
    frontend_url: str,
) -> tuple[str, str]:
    """渲染单条提醒标题与正文（敏感信息不出现）。"""
    title = f"【风险预警 {alert.level}】供应商「{supplier_name}」"
    lines = [
        f"维度：{event_type}",
        f"事件：{(summary or '')[:80]}",
    ]
    if reasons:
        lines.append(f"匹配：{'; '.join(str(r) for r in reasons[:2])}")
    if frontend_url:
        lines.append(f"平台：{frontend_url.rstrip('/')}/#/risk-alerts/{alert.id}")
    return title, "\n".join(lines)


def _render_digest(payloads: list[tuple[str, str]]) -> tuple[str, str]:
    """把多条单条负载合并为一条摘要。"""
    title = f"【风险预警汇总】共 {len(payloads)} 条提醒"
    lines: list[str] = []
    for idx, (_title, _content) in enumerate(payloads[:10], start=1):
        first_line = (_content.splitlines()[0] if _content else "")
        lines.append(f"{idx}. {first_line}")
    if len(payloads) > 10:
        lines.append(f"…另有 {len(payloads) - 10} 条，请登录平台查看")
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# 订阅与免打扰
# ---------------------------------------------------------------------------
def _global_subscription(
    session: Session, settings: NotificationSettings
) -> NotificationSubscription:
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


def _active_channels(
    session: Session, settings: NotificationSettings
) -> list[str]:
    """返回当前应推送的渠道名（.env 启用 且 订阅记录启用）。

    订阅表无任何渠道记录时按 .env 启用的渠道推送（默认行为）。
    """
    providers = {provider.name: provider for provider in build_providers(settings)}
    rows = list(
        session.scalars(
            select(NotificationSubscription).where(
                NotificationSubscription.channel != "global"
            )
        )
    )
    if not rows:
        return sorted(providers)
    enabled = {row.channel for row in rows if row.enabled}
    return sorted(name for name in providers if name in enabled)


def _in_quiet_hours(now: datetime, quiet_hours: dict[str, object] | None) -> bool:
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
        return current >= start or current < end  # 跨天时段（如 22:00–08:00）
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 发送辅助
# ---------------------------------------------------------------------------
def _hourly_sent_count(session: Session, channel: str, now: datetime) -> int:
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


def _send_with_retry(
    provider: NotifyProvider,
    delivery: NotificationDelivery,
    title: str,
    content: str,
    settings: NotificationSettings,
    *,
    now: datetime,
) -> str:
    """尝试发送一次并更新记录；返回 status（success / failed / queued）。"""
    try:
        provider.send(title, content)
        delivery.status = "success"
        delivery.delivered_at = now
        delivery.error = None
        delivery.title = title
        delivery.content = content[:2000]
        return "success"
    except NotificationError as exc:
        delivery.attempt += 1
        delivery.error = str(exc)[:500]
        if delivery.attempt >= settings.retry_attempts:
            delivery.status = "failed"
            return "failed"
        delivery.created_at = now  # 重新进入合并窗口计时，避免每轮重试风暴
        return "queued"


# ---------------------------------------------------------------------------
# 主扫描
# ---------------------------------------------------------------------------
def scan_and_notify(
    session: Session,
    settings: NotificationSettings,
    *,
    now: datetime | None = None,
    providers: list[NotifyProvider] | None = None,
) -> dict[str, int]:
    """执行一轮推送扫描，返回汇总统计（可注入 now/providers 便于测试）。"""
    current = now or datetime.now(UTC)
    summary = {
        "new_alerts": 0,
        "upgraded": 0,
        "sent": 0,
        "merged": 0,
        "queued": 0,
        "failed": 0,
        "rate_limited": 0,
        "quiet_suppressed": 0,
        "channels": 0,
    }
    provider_map = {
        provider.name: provider for provider in (providers or build_providers(settings))
    }
    channels = [
        name for name in _active_channels(session, settings) if name in provider_map
    ]
    summary["channels"] = len(channels)
    if not channels:
        return summary

    global_row = _global_subscription(session, settings)
    push_levels = set(global_row.push_levels or list(settings.push_levels))
    quiet = global_row.quiet_hours

    cursor = session.scalar(select(func.max(NotificationDelivery.alert_id)))
    candidates = list(
        session.scalars(
            select(RiskAlert).where(
                RiskAlert.status == "current",
                RiskAlert.level.in_(push_levels),
            )
        )
    )

    for alert in candidates:
        is_new = cursor is None or alert.id > cursor
        for channel in channels:
            existing = session.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.alert_id == alert.id,
                    NotificationDelivery.channel == channel,
                )
            )
            if existing is not None:
                pushed = existing.pushed_level
                if (
                    existing.status != "success"
                    or pushed is None
                    or LEVEL_ORDER.get(alert.level, 99) >= LEVEL_ORDER.get(pushed, 99)
                ):
                    continue
                summary["upgraded"] += 1
            elif not is_new:
                continue
            else:
                summary["new_alerts"] += 1
            _enqueue_or_send(
                session,
                settings,
                provider_map[channel],
                alert,
                quiet,
                current,
                summary,
                delivery=existing,
            )

    session.flush()
    _process_merge_queue(
        session, settings, provider_map, channels, quiet, current, summary
    )
    session.flush()
    return summary


def _enqueue_or_send(
    session: Session,
    settings: NotificationSettings,
    provider: NotifyProvider,
    alert: RiskAlert,
    quiet: dict[str, object] | None,
    now: datetime,
    summary: dict[str, int],
    *,
    delivery: NotificationDelivery | None,
) -> None:
    """对单个 alert×渠道：P1 即时发送；P2 入合并队列（免打扰/限频抑制）。"""
    supplier_name = "未知供应商"
    event_type = ""
    event_summary = ""
    reasons: list[str] = []
    match = session.get(SupplierEventMatch, alert.match_id)
    if match is not None:
        supplier = session.get(Supplier, match.supplier_id)
        if supplier is not None:
            supplier_name = supplier.legal_name
        reasons = list(match.reasons or [])
        event = session.get(RiskEvent, match.event_id)
        if event is not None:
            event_type = event.event_type
            event_summary = event.summary

    title, content = _render_alert_payload(
        alert,
        supplier_name,
        event_type,
        event_summary,
        reasons,
        settings.frontend_url,
    )

    if delivery is None:
        delivery = NotificationDelivery(
            alert_id=alert.id,
            channel=provider.name,
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

    # 免打扰：仅抑制 P2（P1 始终可达）
    if alert.level == "P2" and _in_quiet_hours(now, quiet):
        delivery.status = "quiet_suppressed"
        summary["quiet_suppressed"] += 1
        return

    if alert.level == "P1":
        _dispatch_immediate(
            session, settings, provider, delivery, title, content, now, summary
        )
    else:
        delivery.status = "queued"
        summary["queued"] += 1


def _dispatch_immediate(
    session: Session,
    settings: NotificationSettings,
    provider: NotifyProvider,
    delivery: NotificationDelivery,
    title: str,
    content: str,
    now: datetime,
    summary: dict[str, int],
) -> None:
    """P1 即时发送；超限时转入合并队列（转合并摘要）。"""
    if _hourly_sent_count(session, provider.name, now) >= settings.hourly_limit:
        delivery.status = "queued"
        summary["rate_limited"] += 1
        return
    status = _send_with_retry(
        provider, delivery, title, content, settings, now=now
    )
    if status == "success":
        summary["sent"] += 1
    elif status == "failed":
        summary["failed"] += 1
    else:
        summary["queued"] += 1


def _process_merge_queue(
    session: Session,
    settings: NotificationSettings,
    provider_map: dict[str, NotifyProvider],
    channels: list[str],
    quiet: dict[str, object] | None,
    now: datetime,
    summary: dict[str, int],
) -> None:
    """合并窗口到期（或强制兜底）的 queued 记录 → 合并摘要发送。"""
    queued = list(
        session.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.status == "queued")
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        )
    )
    for channel in channels:
        provider = provider_map[channel]
        batch = [
            d
            for d in queued
            if d.channel == channel
            and now - d.created_at >= timedelta(minutes=settings.merge_window_minutes)
        ]
        if not batch:
            continue
        # 全部为 P2 且处于免打扰时段 → 抑制留痕
        if _in_quiet_hours(now, quiet) and all(
            (d.pushed_level or "P2") == "P2" for d in batch
        ):
            for d in batch:
                d.status = "quiet_suppressed"
            summary["quiet_suppressed"] += len(batch)
            continue
        # 限频：兜底时长内超限则抑制；有兜底到期则强制发送（防积压）
        if _hourly_sent_count(session, channel, now) >= settings.hourly_limit:
            if all(
                now - d.created_at < timedelta(minutes=QUEUED_FORCE_SEND_MINUTES)
                for d in batch
            ):
                for d in batch:
                    d.status = "rate_limited"
                summary["rate_limited"] += len(batch)
                continue
        payloads = [(d.title or "", d.content or "") for d in batch]
        title, content = _render_digest(payloads)
        first = batch[0]
        status = _send_with_retry(
            provider, first, title, content, settings, now=now
        )
        if status == "success":
            first.alert_id = None  # 摘要是多条合并，不再指向单条 alert
            for d in batch[1:]:
                d.status = "merged"
            summary["merged"] += len(batch)
        elif status == "failed":
            first.status = "failed"
            summary["failed"] += 1
        else:
            first.status = "queued"
            summary["queued"] += 1


# ---------------------------------------------------------------------------
# Scheduler 入口
# ---------------------------------------------------------------------------
def notify_job() -> None:
    """Scheduler 独立 Job：轮询新增/升级提醒并推送。"""
    settings = get_notification_settings()
    if not settings.enabled:
        return
    if not _notify_lock.acquire(blocking=False):
        logger.info("已有通知扫描批次运行，跳过本次")
        return
    try:
        with SessionLocal() as session:
            summary = scan_and_notify(session, settings)
        logger.info("通知扫描完成: %s", summary)
    except Exception:
        logger.exception("通知扫描异常")
    finally:
        _notify_lock.release()
