"""通知扫描编排：逐 (alert_id, channel) 去重扫描 → 防骚扰（合并/限频/免打扰）→ 发送。

- 去重无全局游标；success/merged 同级不重发、升级可重发；failed 终态；
  quiet/rate/expired 抑制在条件解除后复查再投递。
- 摘要成功首条 success、其余 merged，成员保留 alert_id 并统一投递状态；
  失败全批一致 attempt 与线性退避，不改写 created_at。
- 创建早于回填窗口（queries.BACKFILL_GRACE_MINUTES）且无投递记录的候选只计
  backfill_pending（不补发）；alert_id IS NULL 只计 legacy_unlinked。
- notify_job 成功显式 commit、异常显式 rollback：发送后提交失败存在
  at-least-once 重复窗口，下一成功轮收敛，不宣称 exactly-once。
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import NotificationSettings, get_notification_settings
from app.database import SessionLocal
from app.notification import delivery_queries as queries
from app.notification.models import NotificationDelivery
from app.notification.providers import (
    NotificationError,
    NotifyProvider,
    build_providers,
)
from app.notification.rendering import (
    DigestMember,
    alert_context,
    render_alert_payload,
    render_digest,
)
from app.risks.models import RiskAlert
from app.risks.query_validity import current_alert_condition

logger = logging.getLogger("notification")

_notify_lock = threading.Lock()


def scan_and_notify(
    session: Session,
    settings: NotificationSettings,
    *,
    now: datetime | None = None,
    providers: list[NotifyProvider] | None = None,
) -> dict[str, int]:
    """执行一轮推送扫描，返回汇总统计（可注入 now/providers 便于测试）。"""
    current = now or datetime.now(UTC)
    summary: dict[str, int] = dict.fromkeys(
        (
            "new_alerts",
            "upgraded",
            "recovered",
            "sent",
            "merged",
            "queued",
            "failed",
            "rate_limited",
            "quiet_suppressed",
            "expired_suppressed",
            "backfill_pending",
            "legacy_unlinked",
            "channels",
        ),
        0,
    )
    summary["legacy_unlinked"] = queries.count_legacy_unlinked(session)
    provider_map = {
        provider.name: provider for provider in (providers or build_providers(settings))
    }
    channels = queries.active_channels(session, set(provider_map))
    summary["channels"] = len(channels)
    if not channels:
        return summary

    global_row = queries.global_subscription(session, settings)
    push_levels = set(global_row.push_levels or list(settings.push_levels))
    quiet = global_row.quiet_hours
    criteria = (current_alert_condition(current), RiskAlert.level.in_(push_levels))
    candidates = list(
        session.scalars(select(RiskAlert).where(*criteria).order_by(RiskAlert.id))
    )

    for alert in candidates:
        for channel in channels:
            existing = queries.get_delivery(session, alert.id, channel)
            if existing is None:
                if queries.is_backfill(alert, current):
                    summary["backfill_pending"] += 1
                    continue
                summary["new_alerts"] += 1
            else:
                action = queries.plan_redelivery(existing, alert.level)
                if action is None:
                    continue
                summary[action] += 1
            _enqueue_or_send(
                session, settings, provider_map[channel], alert, quiet, current,
                summary, delivery=existing,
            )

    session.flush()
    _process_merge_queue(
        session, settings, provider_map, channels, quiet, current, summary
    )
    session.flush()
    return summary


def _send_with_retry(
    session: Session,
    provider: NotifyProvider,
    delivery: NotificationDelivery,
    title: str,
    content: str,
    settings: NotificationSettings,
    *,
    now: datetime,
) -> str:
    """发送一次并更新记录；返回 summary 键（sent/failed/queued/expired_suppressed）。"""
    if delivery.alert_id is not None:
        alert = queries.get_current_alert(session, delivery.alert_id, now)
        if alert is None:
            delivery.status = "expired_suppressed"
            delivery.error = "alert_no_longer_current_or_valid"
            return "expired_suppressed"
    try:
        provider.send(title, content)
        delivery.status = "success"
        delivery.delivered_at = now
        delivery.error = None
        delivery.title = title
        delivery.content = content[:2000]
        return "sent"
    except NotificationError as exc:
        delivery.attempt += 1
        delivery.error = str(exc)[:500]
        if delivery.attempt >= settings.retry_attempts:
            delivery.status = "failed"
            return "failed"
        # 退避锚点：显式写本轮失败时刻（服务端 onupdate 同 Session 内不可回读）
        delivery.updated_at = now
        delivery.status = "queued"  # 重新进入合并队列；退避见 queries.merge_due
        return "queued"


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
    """对单个 alert×渠道渲染并投递：P1 即时发送；P2 入合并队列（免打扰/限频抑制）。"""
    supplier_name, event_type, event_summary, reasons = alert_context(session, alert)
    title, content = render_alert_payload(
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

    # 免打扰/限频判定：immediate 之外的取值同时是 summary 键
    action = queries.classify_dispatch(session, provider, alert, quiet, now, settings)
    if action == "immediate":
        action = _send_with_retry(
            session, provider, delivery, title, content, settings, now=now
        )
    else:
        # 限频 P1 转合并队列（行回 queued 等待摘要）；quiet_suppressed 留痕
        delivery.status = "queued" if action == "rate_limited" else action
    summary[action] += 1


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
        batch = queries.prepare_merge_batch(
            session, queued, channel, settings, quiet, now, summary
        )
        if not batch:
            continue
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
        if not current_batch:
            continue
        title, content = render_digest(members, settings.frontend_url)
        try:
            provider.send(title, content)
        except NotificationError as exc:
            queries.mark_batch_failure(
                current_batch, exc, settings.retry_attempts, now, summary
            )
            continue
        queries.mark_batch_success(current_batch, title, content, now, summary)


def notify_job() -> None:
    """Scheduler 独立 Job：轮询新增/升级提醒并推送；成功提交、异常回滚。"""
    settings = get_notification_settings()
    if not settings.enabled:
        return
    if not _notify_lock.acquire(blocking=False):
        logger.info("已有通知扫描批次运行，跳过本次")
        return
    try:
        with SessionLocal() as session:
            try:
                summary = scan_and_notify(session, settings)
                session.commit()
            except Exception:
                session.rollback()
                raise
        logger.info("通知扫描完成: %s", summary)
    except Exception:
        logger.exception("通知扫描异常")
    finally:
        _notify_lock.release()
