"""风险月报测试：时间窗口、统计口径、渲染、同口径、幂等与失败重试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import NotificationSettings, get_notification_settings
from app.notification.monthly_report import (
    MAX_ATTEMPTS,
    P1_ITEM_LIMIT,
    MonthStats,
    P1Item,
    collect_month_stats,
    monthly_subscribers,
    previous_month_window,
    render_monthly_report,
    run_monthly_reports,
)
from app.notification.providers import NotificationError
from app.notification.user_models import (
    NotificationMonthlyReport,
    UserNotificationSubscription,
)
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
SEPTEMBER_START = datetime(2026, 9, 1, tzinfo=SHANGHAI).astimezone(UTC)
OCTOBER_START = datetime(2026, 10, 1, tzinfo=SHANGHAI).astimezone(UTC)
INSIDE = datetime(2026, 9, 15, 12, 0, tzinfo=SHANGHAI).astimezone(UTC)

_SEVERITY = {"P1": "critical", "P2": "high", "P3": "medium", "P4": "low"}

_counter = 0
_user_counter = 0


class RecordingSender:
    """捕获发信调用，不建立 SMTP 连接；可模拟前 N 次失败。"""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_times = fail_times

    def __call__(self, email: str, title: str, content: str) -> None:
        self.calls.append((email, title, content))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise NotificationError("邮件发送失败: FakeSMTP")


def make_settings(frontend_url: str = "https://risk.example.com") -> NotificationSettings:
    return replace(
        get_notification_settings(),
        enabled=True,
        email_enabled=True,
        frontend_url=frontend_url,
        retry_attempts=2,
    )


def make_supplier(session: Session, name: str) -> Supplier:
    global _counter
    _counter += 1
    supplier = Supplier(
        supplier_code=f"REPORT-{_counter:05d}",
        legal_name=name,
        country_code="CN",
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    return supplier


def make_alert(
    session: Session,
    *,
    level: str = "P1",
    event_type: str = "经营异常",
    supplier: Supplier | None = None,
    status: str = "current",
    created_at: datetime | None = None,
) -> RiskAlert:
    """参考 test_notifications.make_alert，额外支持指定供应商与 created_at。"""
    global _counter
    _counter += 1
    if supplier is None:
        supplier = Supplier(
            supplier_code=f"REPORT-{_counter:05d}",
            legal_name=f"月报供应商{_counter}",
            country_code="CN",
            enabled=True,
        )
        session.add(supplier)
    event = RiskEvent(
        dedup_key=f"report-event-{_counter:05d}",
        event_type=event_type,
        severity=_SEVERITY[level],
        summary="月报统计用事件",
        confidence=0.9,
        facts={},
    )
    session.add(event)
    session.flush()
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="entity",
        score=95,
        reasons=[],
        evidence=[],
    )
    session.add(match)
    session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level=level,
        score=95,
        score_detail={"rule_version": "v1"},
        status=status,
    )
    session.add(alert)
    session.flush()
    # created_at 由 server_default 生成，聚合口径需要可控值，创建后显式覆盖。
    alert.created_at = created_at or INSIDE
    session.flush()
    return alert


def make_user(
    session: Session,
    *,
    role: str = "risk_admin",
    email: str | None = "user@example.com",
    status: str = "active",
    monthly: bool = True,
) -> User:
    global _user_counter
    _user_counter += 1
    user = User(
        username=f"report-user-{_user_counter}",
        password_hash="not-used-by-test",
        display_name=f"月报用户{_user_counter}",
        email=email,
        role=role,
        status=status,
    )
    session.add(user)
    session.flush()
    session.add(
        UserNotificationSubscription(user_id=user.id, push_monthly_report=monthly)
    )
    session.flush()
    return user


def monthly_records(session: Session, period: str = "2026-09") -> list[NotificationMonthlyReport]:
    return list(
        session.scalars(
            select(NotificationMonthlyReport).where(
                NotificationMonthlyReport.period == period
            )
        )
    )


def test_previous_month_window_normal_month() -> None:
    """普通月：本地 9 月 1 日 00:00 对应 UTC 8 月 31 日 16:00。"""
    period, start, end = previous_month_window(NOW)

    assert period == "2026-09"
    assert start == SEPTEMBER_START
    assert end == OCTOBER_START
    assert start == (datetime(2026, 9, 1) - timedelta(hours=8)).replace(tzinfo=UTC)
    assert start == datetime(2026, 8, 31, 16, tzinfo=UTC)
    assert end == datetime(2026, 9, 30, 16, tzinfo=UTC)


def test_previous_month_window_cross_year() -> None:
    """跨年：2026-01 的上一个月是 2025-12。"""
    period, start, end = previous_month_window(datetime(2026, 1, 5, 1, 0, tzinfo=UTC))

    assert period == "2025-12"
    assert start == datetime(2025, 11, 30, 16, tzinfo=UTC)
    assert end == datetime(2025, 12, 31, 16, tzinfo=UTC)


def test_previous_month_window_uses_local_month() -> None:
    """UTC 9 月 30 日 16:30 已是本地 10 月 1 日，上月应为 9 月而非 8 月。"""
    period, _start, end = previous_month_window(
        datetime(2026, 9, 30, 16, 30, tzinfo=UTC)
    )

    assert period == "2026-09"
    assert end == datetime(2026, 9, 30, 16, tzinfo=UTC)


def test_collect_month_stats_only_p1_p3_in_window(db_session: Session) -> None:
    """只统计 P1/P2/P3 且只含窗口内；窗口外、P4 排除，expired 仍计入。"""
    make_alert(db_session, level="P1")
    make_alert(db_session, level="P2")
    make_alert(db_session, level="P3", status="expired")
    make_alert(db_session, level="P4")
    make_alert(db_session, level="P1", created_at=SEPTEMBER_START - timedelta(seconds=1))
    make_alert(db_session, level="P1", created_at=OCTOBER_START)

    stats = collect_month_stats(db_session, SEPTEMBER_START, OCTOBER_START)

    assert stats.period == "2026-09"
    assert stats.total == 3
    assert stats.by_level == {"P1": 1, "P2": 1, "P3": 1}


def test_collect_month_stats_aggregates(db_session: Session) -> None:
    """供应商去重、维度 Top5 降序（同数按名称升序）、P1 明细上限 10。"""
    shared = make_supplier(db_session, "共享供应商")
    plan = (
        [("经营异常", "P1")] * 5
        + [("司法诉讼", "P1")] * 4
        + [("政策变化", "P1")] * 4
        + [("财务风险", "P1")] * 2
        + [("税务", "P2")]
        + [("舆情", "P2")]
    )
    for index, (event_type, level) in enumerate(plan):
        supplier = shared if index < 12 else make_supplier(db_session, f"独有供应商{index}")
        make_alert(db_session, level=level, event_type=event_type, supplier=supplier)

    stats = collect_month_stats(db_session, SEPTEMBER_START, OCTOBER_START)

    assert stats.total == 17
    assert stats.by_level == {"P1": 15, "P2": 2, "P3": 0}
    assert stats.supplier_count == 6
    assert stats.top_dimensions == [
        ("经营异常", 5),
        ("司法诉讼", 4),
        ("政策变化", 4),
        ("财务风险", 2),
        ("税务", 1),
    ]
    assert len(stats.p1_items) == P1_ITEM_LIMIT
    assert stats.p1_items[0].supplier_name == "共享供应商"


def test_render_monthly_report() -> None:
    """标题无前导零、窗口末日取上月最后一天、正文含 P1 链接。"""
    stats = MonthStats(
        period="2026-09",
        total=18,
        by_level={"P1": 3, "P2": 9, "P3": 6},
        supplier_count=11,
        top_dimensions=[("经营异常", 5), ("司法诉讼", 4)],
        p1_items=[P1Item("江苏聚力材料", "经营异常", 123)],
    )

    title, content = render_monthly_report(stats, "https://risk.example.com/")
    lines = content.splitlines()

    assert title == "【供应商风险】2026 年 9 月风险月报"
    assert lines[:8] == [
        "统计范围：2026-09-01 ~ 2026-09-30",
        "",
        "新增风险提醒 18 条",
        "  P1：3 条    P2：9 条    P3：6 条",
        "影响供应商 11 家",
        "风险维度 Top5：经营异常 5、司法诉讼 4",
        "",
        "重点关注（P1）：",
    ]
    assert "1. 江苏聚力材料｜经营异常｜平台：https://risk.example.com/risks/123" in lines
    assert lines[-1] == "本邮件由系统自动发送，数据口径为平台内当前保留的提醒记录。"


def test_render_monthly_report_empty_and_without_link() -> None:
    """无维度显示「无」，无 P1 不输出重点关注，frontend_url 为空省略链接。"""
    stats = MonthStats(
        period="2026-02",
        total=0,
        by_level={"P1": 0, "P2": 0, "P3": 0},
        supplier_count=0,
        top_dimensions=[],
        p1_items=[],
    )

    title, content = render_monthly_report(stats, "")

    assert title == "【供应商风险】2026 年 2 月风险月报"
    assert "统计范围：2026-02-01 ~ 2026-02-28" in content
    assert "风险维度 Top5：无" in content
    assert "重点关注" not in content


def test_all_subscribers_receive_identical_content(db_session: Session) -> None:
    """全员同口径：两个不同角色的订阅者收到完全一致的标题与正文。"""
    admin = make_user(db_session, role="platform_admin", email="admin@example.com")
    analyst = make_user(db_session, role="risk_analyst", email="analyst@example.com")
    make_alert(db_session, level="P1")
    sender = RecordingSender()

    summary = run_monthly_reports(db_session, make_settings(), now=NOW, sender=sender)

    assert summary["targets"] == 2
    assert summary["sent"] == 2
    assert {call[0] for call in sender.calls} == {
        "admin@example.com",
        "analyst@example.com",
    }
    assert len({call[1] for call in sender.calls}) == 1
    assert len({call[2] for call in sender.calls}) == 1
    records = monthly_records(db_session)
    assert {record.user_id for record in records} == {admin.id, analyst.id}
    assert all(record.status == "success" for record in records)
    assert all(record.content == sender.calls[0][2] for record in records)


def test_monthly_subscribers_filters(db_session: Session) -> None:
    """只发给 active、非 viewer、开启月报且邮箱合法者。"""
    valid = make_user(db_session, email="valid@example.com")
    make_user(db_session, email="off@example.com", monthly=False)
    make_user(db_session, email="pending@example.com", status="pending")
    make_user(db_session, email="viewer@example.com", role="viewer")
    make_user(db_session, email="bad-address")
    make_user(db_session, email=None)

    assert monthly_subscribers(db_session) == [(valid.id, "valid@example.com")]


def test_run_monthly_reports_is_idempotent(db_session: Session) -> None:
    """连续两次运行：第二次 already_done 且不重复发送、只留一条记录。"""
    make_user(db_session, email="monthly@example.com")
    make_alert(db_session, level="P2")
    sender = RecordingSender()

    first = run_monthly_reports(db_session, make_settings(), now=NOW, sender=sender)
    second = run_monthly_reports(db_session, make_settings(), now=NOW, sender=sender)

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert second["already_done"] == 1
    assert len(sender.calls) == 1
    assert len(monthly_records(db_session)) == 1


def test_run_monthly_reports_skips_without_data(db_session: Session) -> None:
    """窗口内无 P1/P2/P3：留痕 skipped_no_data，不发信，下一轮 already_done。"""
    make_user(db_session, email="monthly@example.com")
    make_alert(db_session, level="P4")
    make_alert(db_session, level="P1", created_at=OCTOBER_START)
    sender = RecordingSender()

    summary = run_monthly_reports(db_session, make_settings(), now=NOW, sender=sender)
    records = monthly_records(db_session)

    assert summary["skipped_no_data"] == 1
    assert summary["sent"] == 0
    assert sender.calls == []
    assert [record.status for record in records] == ["skipped_no_data"]

    again = run_monthly_reports(db_session, make_settings(), now=NOW, sender=sender)
    assert again["already_done"] == 1
    assert again["skipped_no_data"] == 0
    assert sender.calls == []


def test_failed_send_recorded_and_retried(db_session: Session) -> None:
    """发送失败：status=failed、attempt=1、error 无凭据；下一轮重试成功。"""
    make_user(db_session, email="monthly@example.com")
    make_alert(db_session, level="P1")

    first = run_monthly_reports(
        db_session, make_settings(), now=NOW, sender=RecordingSender(fail_times=1)
    )
    record = monthly_records(db_session)[0]

    assert first["failed"] == 1
    assert first["sent"] == 0
    assert record.status == "failed"
    assert record.attempt == 1
    assert record.error == "邮件发送失败: FakeSMTP"

    retry_sender = RecordingSender()
    second = run_monthly_reports(
        db_session, make_settings(), now=NOW, sender=retry_sender
    )
    db_session.refresh(record)

    assert second["sent"] == 1
    assert len(retry_sender.calls) == 1
    assert record.status == "success"
    assert record.attempt == 1
    assert record.sent_at is not None
    assert record.error is None


def test_attempt_cap_skips_send(db_session: Session) -> None:
    """达到 MAX_ATTEMPTS 的失败记录不再重试。"""
    user = make_user(db_session, email="monthly@example.com")
    make_alert(db_session, level="P1")
    db_session.add(
        NotificationMonthlyReport(
            user_id=user.id,
            period="2026-09",
            status="failed",
            attempt=MAX_ATTEMPTS,
        )
    )
    db_session.flush()
    sender = RecordingSender()

    summary = run_monthly_reports(db_session, make_settings(), now=NOW, sender=sender)

    assert sender.calls == []
    assert summary["sent"] == 0
    assert summary["failed"] == 0


def test_disabled_settings_returns_empty_summary(db_session: Session) -> None:
    """邮件总开关关闭时不统计、不发送。"""
    make_user(db_session, email="monthly@example.com")
    make_alert(db_session, level="P1")
    sender = RecordingSender()

    summary = run_monthly_reports(
        db_session,
        replace(make_settings(), email_enabled=False),
        now=NOW,
        sender=sender,
    )

    assert summary == {
        "targets": 0,
        "sent": 0,
        "skipped_no_data": 0,
        "failed": 0,
        "already_done": 0,
    }
    assert sender.calls == []
