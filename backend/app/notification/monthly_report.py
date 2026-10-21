"""风险月报：上月（Asia/Shanghai）统计、渲染与定时投递。

全员同口径只统计一次，只统计 P1/P2/P3；按 (user_id, period) 幂等。
"""

from __future__ import annotations

import calendar
import logging
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import NotificationSettings, get_notification_settings
from app.database import SessionLocal
from app.notification.email_provider import EmailProvider
from app.notification.providers import NotificationError
from app.notification.rendering import detail_link
from app.notification.user_models import (
    NotificationMonthlyReport,
    UserNotificationSubscription,
)
from app.notification.user_targets import SKIPPED_ROLE
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier

logger = logging.getLogger("notification")

REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
REPORT_LEVELS = ("P1", "P2", "P3")  # P4 无推送价值，月报不统计
MAX_ATTEMPTS = 5
TOP_DIMENSION_LIMIT = 5
P1_ITEM_LIMIT = 10
DONE_STATUSES = ("success", "skipped_no_data")  # 终态：后续轮次直接跳过
SUMMARY_KEYS = ("targets", "sent", "skipped_no_data", "failed", "already_done")

_monthly_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class P1Item:
    supplier_name: str
    event_type: str
    alert_id: int


@dataclass(frozen=True, slots=True)
class MonthStats:
    period: str
    total: int
    by_level: dict[str, int]
    supplier_count: int
    top_dimensions: list[tuple[str, int]]
    p1_items: list[P1Item]


def previous_month_window(now_utc: datetime) -> tuple[str, datetime, datetime]:
    """返回 (period, start_utc, end_utc)：上月自然月，本地 00:00 换算 UTC。"""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    local_now = now_utc.astimezone(REPORT_TIMEZONE)
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 本月 1 日减 1 天再取 1 日即上月 1 日；1 月会自然回退到上年 12 月。
    previous_start = (month_start - timedelta(days=1)).replace(day=1)
    period = f"{previous_start.year:04d}-{previous_start.month:02d}"
    return period, previous_start.astimezone(UTC), month_start.astimezone(UTC)


def collect_month_stats(
    session: Session, start_utc: datetime, end_utc: datetime
) -> MonthStats:
    """一次 join 查询后在内存聚合，避免按用户或按维度多次往返。"""
    rows = session.execute(
        select(
            RiskAlert.id,
            RiskAlert.level,
            SupplierEventMatch.supplier_id,
            RiskEvent.event_type,
            Supplier.legal_name,
        )
        .join(SupplierEventMatch, SupplierEventMatch.id == RiskAlert.match_id)
        .join(RiskEvent, RiskEvent.id == SupplierEventMatch.event_id)
        .join(Supplier, Supplier.id == SupplierEventMatch.supplier_id)
        .where(
            RiskAlert.created_at >= start_utc,
            RiskAlert.created_at < end_utc,
            RiskAlert.level.in_(REPORT_LEVELS),
        )
        .order_by(RiskAlert.id)
    ).all()
    by_level: dict[str, int] = dict.fromkeys(REPORT_LEVELS, 0)
    dimensions: Counter[str] = Counter(row.event_type for row in rows)
    p1_items: list[P1Item] = []
    # 不过滤 status：窗口内创建、如今已 expired 的提醒仍属「本月新增」。
    for row in rows:
        by_level[row.level] = by_level.get(row.level, 0) + 1
        if row.level == "P1" and len(p1_items) < P1_ITEM_LIMIT:
            p1_items.append(P1Item(row.legal_name, row.event_type, row.id))
    top_dimensions = sorted(
        dimensions.items(), key=lambda item: (-item[1], item[0])
    )[:TOP_DIMENSION_LIMIT]
    return MonthStats(
        period=start_utc.astimezone(REPORT_TIMEZONE).strftime("%Y-%m"),
        total=len(rows),
        by_level=by_level,
        supplier_count=len({row.supplier_id for row in rows}),
        top_dimensions=top_dimensions,
        p1_items=p1_items,
    )


def render_monthly_report(stats: MonthStats, frontend_url: str) -> tuple[str, str]:
    """渲染月报标题与纯文本正文；frontend_url 为空时省略详情链接。"""
    year, month = (int(part) for part in stats.period.split("-"))
    # 窗口末日取「上月最后一天」，不能用本月 1 日展示。
    last_day = calendar.monthrange(year, month)[1]
    detail = "、".join(f"{name} {count}" for name, count in stats.top_dimensions)
    dimension_line = f"风险维度 Top5：{detail}" if detail else "风险维度 Top5：无"
    lines = [
        f"统计范围：{year:04d}-{month:02d}-01 ~ {year:04d}-{month:02d}-{last_day:02d}",
        "",
        f"新增风险提醒 {stats.total} 条",
        f"  P1：{stats.by_level.get('P1', 0)} 条    "
        f"P2：{stats.by_level.get('P2', 0)} 条    "
        f"P3：{stats.by_level.get('P3', 0)} 条",
        f"影响供应商 {stats.supplier_count} 家",
        dimension_line,
    ]
    if stats.p1_items:
        lines.extend(["", "重点关注（P1）："])
        for index, item in enumerate(stats.p1_items, start=1):
            line = f"{index}. {item.supplier_name}｜{item.event_type}"
            if frontend_url:
                line += f"｜平台：{detail_link(frontend_url, item.alert_id)}"
            lines.append(line)
    lines.extend(["", "本邮件由系统自动发送，数据口径为平台内当前保留的提醒记录。"])
    return f"【供应商风险】{year} 年 {month} 月风险月报", "\n".join(lines)


def _is_valid_email(email: str) -> bool:
    """与 email_provider._validate_recipient 同口径：单地址、无空白/分隔符。"""
    cleaned = email.strip()
    if (
        cleaned.count("@") != 1
        or "," in cleaned
        or ";" in cleaned
        or any(character.isspace() for character in cleaned)
    ):
        return False
    local_part, _, domain = cleaned.rpartition("@")
    return bool(local_part) and bool(domain)


def monthly_subscribers(session: Session) -> list[tuple[int, str]]:
    """月报收件人：active、非 viewer、开启月报且邮箱合法；按 user_id 排序。"""
    rows = session.execute(
        select(User.id, User.email)
        .join(UserNotificationSubscription, UserNotificationSubscription.user_id == User.id)
        .where(
            User.status == "active", User.role != SKIPPED_ROLE,
            UserNotificationSubscription.push_monthly_report.is_(True),
            User.email.is_not(None),
        )
        .order_by(User.id)
    ).all()
    subscribers: list[tuple[int, str]] = []
    for user_id, raw_email in rows:
        email = (raw_email or "").strip()
        if _is_valid_email(email):
            subscribers.append((user_id, email))
        else:
            # 日志只保留 user_id，禁止写入邮箱原文。
            logger.warning("月报跳过 user_id=%s：邮箱缺失或格式非法", user_id)
    return subscribers


def run_monthly_reports(
    session: Session,
    settings: NotificationSettings,
    *,
    now: datetime | None = None,
    sender: Callable[[str, str, str], None] | None = None,
) -> dict[str, int]:
    """生成并投递上月月报：全库统计一次 → 逐用户幂等发送；不提交事务。"""
    current = now or datetime.now(UTC)
    summary: dict[str, int] = dict.fromkeys(SUMMARY_KEYS, 0)
    if not settings.enabled or not settings.email_enabled:
        return summary
    period, start_utc, end_utc = previous_month_window(current)
    subscribers = monthly_subscribers(session)
    summary["targets"] = len(subscribers)
    if not subscribers:
        return summary
    records = {
        record.user_id: record
        for record in session.scalars(
            select(NotificationMonthlyReport).where(NotificationMonthlyReport.period == period)
        )
    }
    stats = collect_month_stats(session, start_utc, end_utc)
    if stats.total == 0:
        # 无数据：为所有订阅者留痕 skipped_no_data，不发信。
        for user_id, _email in subscribers:
            record = records.get(user_id)
            if record is not None and record.status in DONE_STATUSES:
                summary["already_done"] += 1
                continue
            if record is None:
                record = NotificationMonthlyReport(
                    user_id=user_id, period=period, attempt=0
                )
                session.add(record)
                records[user_id] = record
            record.status, record.error = "skipped_no_data", None
            summary["skipped_no_data"] += 1
        session.flush()
        return summary
    # 全员同口径：标题与正文只渲染一次，所有订阅者复用同一份内容。
    title, content = render_monthly_report(stats, settings.frontend_url)
    for user_id, email in subscribers:
        record = records.get(user_id)
        if record is not None and record.status in DONE_STATUSES:
            summary["already_done"] += 1
            continue
        if record is not None and record.attempt >= MAX_ATTEMPTS:
            continue
        if record is None:
            # 新建行显式 attempt=0，未 flush 时失败分支也能累加。
            record = NotificationMonthlyReport(
                user_id=user_id, period=period, attempt=0
            )
            session.add(record)
            records[user_id] = record
        try:
            if sender is not None:
                sender(email, title, content)
            else:
                EmailProvider(settings, recipient=email).send(title, content)
        except NotificationError as exc:
            record.status = "failed"
            record.attempt += 1
            record.error = str(exc)[:500]
            summary["failed"] += 1
            continue
        record.status = "success"
        record.sent_at = current
        record.title = title
        record.content = content
        record.error = None
        summary["sent"] += 1
    session.flush()
    return summary


def monthly_report_job() -> None:
    """Scheduler 独立 Job：发送上月月报；成功提交、异常回滚。"""
    settings = get_notification_settings()
    if not settings.enabled or not settings.email_enabled:
        return
    if not _monthly_lock.acquire(blocking=False):
        logger.info("上一轮月报推送仍在运行，跳过本次")
        return
    try:
        with SessionLocal() as session:
            try:
                summary = run_monthly_reports(session, settings)
                session.commit()
            except Exception:
                session.rollback()
                raise
        logger.info("风险月报推送完成: %s", summary)
    except Exception:
        logger.exception("风险月报推送异常")
    finally:
        _monthly_lock.release()
