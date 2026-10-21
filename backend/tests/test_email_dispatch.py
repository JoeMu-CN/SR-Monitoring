"""邮件按用户投递测试（《风险提醒邮件推送接入方案.md》批次 2 / 7.2–7.6）。

覆盖：级别过滤、负责供应商过滤、空归属不降级、viewer 跳过、per-user 去重、
分组摘要、单用户限频。全部发送通过 monkeypatch 的 EmailProvider.send 捕获，
不建立任何真实 SMTP 连接。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_notifications import FakeProvider, make_alert

from app.auth.models import User
from app.config import NotificationSettings, get_notification_settings
from app.notification.email_provider import EmailProvider
from app.notification.models import NotificationDelivery
from app.notification.service import scan_and_notify
from app.notification.user_models import (
    UserNotificationSubscription,
    UserSupplierAssignment,
)
from app.risks.models import RiskAlert, SupplierEventMatch

# 合并窗口基准：投递记录 created_at 由数据库生成，需以真实当前时间为参照。
T0 = datetime.now(UTC)

_username_counter = 0


def make_settings(**overrides: object) -> NotificationSettings:
    """以真实配置为底，开启邮件并固定合并窗口/限频，便于断言。"""
    base = get_notification_settings()
    defaults: dict[str, object] = {
        "enabled": True,
        "merge_window_minutes": 15,
        "hourly_limit": 20,
        "retry_attempts": 2,
        "frontend_url": "https://risk.example.com",
        "email_enabled": True,
        "email_smtp_host": "smtp.example.com",
        "email_from": "risk@example.com",
        "email_user_hourly_limit": 20,
        "email_global_hourly_limit": 60,
    }
    defaults.update(overrides)
    return replace(base, **defaults)  # type: ignore[arg-type]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """捕获邮件发送（收件人, 标题, 正文），替代真实 SMTP。"""
    calls: list[tuple[str, str, str]] = []

    def fake_send(
        self: EmailProvider, title: str, content: str, *, timeout: float | None = None
    ) -> None:
        del timeout
        calls.append((self._recipient, title, content))

    monkeypatch.setattr(EmailProvider, "send", fake_send)
    return calls


def make_user(
    session: Session,
    *,
    role: str = "risk_analyst",
    email: str | None = "analyst@example.com",
    status: str = "active",
) -> User:
    global _username_counter
    _username_counter += 1
    user = User(
        username=f"email-target-{_username_counter}",
        password_hash="not-used-by-test",
        display_name=f"收件人{_username_counter}",
        email=email,
        role=role,
        status=status,
    )
    session.add(user)
    session.flush()
    return user


def subscribe(
    session: Session,
    user: User,
    *,
    p1: bool = False,
    p2: bool = False,
    p3: bool = False,
    monthly: bool = False,
) -> UserNotificationSubscription:
    row = UserNotificationSubscription(
        user_id=user.id,
        push_p1=p1,
        push_p2=p2,
        push_p3=p3,
        push_monthly_report=monthly,
    )
    session.add(row)
    session.flush()
    return row


def assign(session: Session, user: User, *supplier_ids: int) -> None:
    for supplier_id in supplier_ids:
        session.add(
            UserSupplierAssignment(user_id=user.id, supplier_id=supplier_id)
        )
    session.flush()


def supplier_id_of(session: Session, alert: RiskAlert) -> int:
    match = session.get(SupplierEventMatch, alert.match_id)
    assert match is not None
    return match.supplier_id


def email_deliveries(
    session: Session, alert_id: int, user_id: int | None = None
) -> list[NotificationDelivery]:
    conditions = [
        NotificationDelivery.alert_id == alert_id,
        NotificationDelivery.channel == "email",
    ]
    if user_id is not None:
        conditions.append(NotificationDelivery.user_id == user_id)
    return list(
        session.scalars(
            select(NotificationDelivery).where(*conditions).order_by(
                NotificationDelivery.id
            )
        )
    )


def scan(
    session: Session,
    settings: NotificationSettings | None = None,
    *,
    now: datetime = T0,
) -> dict[str, int]:
    """固定渠道用假 Provider（避免外网），邮件由 settings 判定启用。"""
    return scan_and_notify(
        session, settings or make_settings(), now=now, providers=[FakeProvider()]
    )


def test_level_switch_limits_each_user(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """勾 P1 只收 P1、勾 P2 只收 P2；未勾选的级别不建记录。"""
    p1_user = make_user(db_session, role="risk_admin", email="p1@example.com")
    subscribe(db_session, p1_user, p1=True)
    p2_user = make_user(db_session, role="risk_admin", email="p2@example.com")
    subscribe(db_session, p2_user, p2=True)

    p1_alert = make_alert(db_session, level="P1")
    scan(db_session)

    assert [recipient for recipient, _title, _content in sent] == ["p1@example.com"]
    assert email_deliveries(db_session, p1_alert.id, p2_user.id) == []

    sent.clear()
    p2_alert = make_alert(db_session, level="P2")
    scan(db_session, now=T0 + timedelta(minutes=1))
    assert sent == []  # P2 先入合并队列，窗口内不发
    assert email_deliveries(db_session, p2_alert.id, p1_user.id) == []

    scan(db_session, now=T0 + timedelta(minutes=20))
    assert [recipient for recipient, _title, _content in sent] == ["p2@example.com"]
    records = email_deliveries(db_session, p2_alert.id)
    assert [(row.user_id, row.status) for row in records] == [(p2_user.id, "success")]


def test_risk_analyst_only_receives_assigned_supplier(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """analyst 负责 A：A 的提醒发送，B 的提醒不发送且不建记录。"""
    analyst = make_user(db_session, email="analyst@example.com")
    subscribe(db_session, analyst, p1=True)
    alert_a = make_alert(db_session, level="P1", legal_name="甲供应商")
    alert_b = make_alert(db_session, level="P1", legal_name="乙供应商")
    assign(db_session, analyst, supplier_id_of(db_session, alert_a))

    scan(db_session)

    assert len(sent) == 1
    assert "甲供应商" in sent[0][1]
    assert len(email_deliveries(db_session, alert_a.id, analyst.id)) == 1
    assert email_deliveries(db_session, alert_b.id) == []


def test_risk_analyst_without_assignment_receives_nothing(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """D4：未分配供应商的分析员即使勾了 P1 也不推送，且不产生投递记录。"""
    analyst = make_user(db_session, email="analyst@example.com")
    subscribe(db_session, analyst, p1=True)
    alert = make_alert(db_session, level="P1")

    summary = scan(db_session)

    assert summary["email_targets"] == 0
    assert sent == []
    assert email_deliveries(db_session, alert.id) == []


def test_risk_admin_receives_any_supplier(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """risk_admin 按全量处理：任意供应商的提醒都发送。"""
    admin = make_user(db_session, role="risk_admin", email="admin@example.com")
    subscribe(db_session, admin, p1=True)
    first = make_alert(db_session, level="P1", legal_name="甲供应商")
    second = make_alert(db_session, level="P1", legal_name="乙供应商")

    scan(db_session)

    assert len(sent) == 2
    for alert in (first, second):
        records = email_deliveries(db_session, alert.id, admin.id)
        assert len(records) == 1
        assert records[0].status == "success"


def test_viewer_never_receives_even_with_subscription(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """viewer 即使库里有勾选记录也不投递（D2）。"""
    viewer = make_user(db_session, role="viewer", email="viewer@example.com")
    subscribe(db_session, viewer, p1=True, p2=True, p3=True, monthly=True)
    alert = make_alert(db_session, level="P1")

    summary = scan(db_session)

    assert summary["email_targets"] == 0
    assert sent == []
    assert email_deliveries(db_session, alert.id) == []


def test_backfill_window_skips_historical_alerts(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """首次开启邮件不补发基线窗口（24h）前的历史提醒，避免上线即被旧风险轰炸。"""
    admin = make_user(db_session, role="risk_admin", email="admin@example.com")
    subscribe(db_session, admin, p1=True)
    alert = make_alert(db_session, level="P1")
    alert.created_at = T0 - timedelta(hours=25)
    db_session.flush()

    summary = scan_and_notify(db_session, make_settings(), now=T0, providers=[])

    assert sent == []
    assert email_deliveries(db_session, alert.id) == []
    assert summary["backfill_pending"] == 1


def test_same_alert_not_resent_per_user(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """同一 alert 两轮扫描：每用户仅一条记录、只发送一次。"""
    analyst = make_user(db_session, role="risk_admin", email="admin@example.com")
    subscribe(db_session, analyst, p1=True)
    alert = make_alert(db_session, level="P1")

    scan(db_session)
    scan(db_session, now=T0 + timedelta(minutes=5))

    assert len(sent) == 1
    records = email_deliveries(db_session, alert.id, analyst.id)
    assert len(records) == 1
    assert records[0].status == "success"


def test_p2_digest_grouped_per_user(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """窗口内多条 P2：每个用户各收一封自己的摘要。"""
    first = make_user(db_session, role="risk_admin", email="first@example.com")
    second = make_user(db_session, role="risk_admin", email="second@example.com")
    subscribe(db_session, first, p2=True)
    subscribe(db_session, second, p2=True)
    make_alert(db_session, level="P2", legal_name="甲供应商")
    make_alert(db_session, level="P2", legal_name="乙供应商")

    scan(db_session)
    assert sent == []

    scan(db_session, now=T0 + timedelta(minutes=20))

    assert sorted(recipient for recipient, _title, _content in sent) == [
        "first@example.com",
        "second@example.com",
    ]
    for _recipient, title, content in sent:
        assert "共 2 条提醒" in title
        assert "甲供应商" in content and "乙供应商" in content


def test_user_hourly_limit_moves_p1_to_queue(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """单用户达到 email_user_hourly_limit 后 P1 转 queued 且不发送。"""
    analyst = make_user(db_session, role="risk_admin", email="admin@example.com")
    subscribe(db_session, analyst, p1=True)
    db_session.add(
        NotificationDelivery(
            alert_id=None,
            channel="email",
            user_id=analyst.id,
            status="success",
            title="占位",
            content="占位",
            delivered_at=T0 - timedelta(minutes=30),
        )
    )
    db_session.flush()
    alert = make_alert(db_session, level="P1")

    summary = scan(db_session, make_settings(email_user_hourly_limit=1))

    assert sent == []
    assert summary["rate_limited"] >= 1
    records = email_deliveries(db_session, alert.id, analyst.id)
    assert len(records) == 1
    assert records[0].status == "queued"


def test_email_disabled_skips_dispatch(
    db_session: Session, sent: list[tuple[str, str, str]]
) -> None:
    """邮件总开关关闭时不加载收件人，也不产生 email 投递记录。"""
    analyst = make_user(db_session, role="risk_admin", email="admin@example.com")
    subscribe(db_session, analyst, p1=True)
    alert = make_alert(db_session, level="P1")

    summary = scan(
        db_session,
        make_settings(email_enabled=False, email_smtp_host="", email_from=""),
    )

    assert summary["email_targets"] == 0
    assert sent == []
    assert email_deliveries(db_session, alert.id) == []


def test_invalid_email_skipped_without_logging_address(
    db_session: Session,
    sent: list[tuple[str, str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """邮箱非法时跳过该用户，日志只含 user_id 与原因，不含邮箱原文。"""
    broken = make_user(db_session, role="risk_admin", email="not-an-email")
    subscribe(db_session, broken, p1=True)
    make_alert(db_session, level="P1")

    with caplog.at_level("WARNING", logger="notification"):
        summary = scan(db_session)

    assert summary["email_targets"] == 0
    assert sent == []
    assert str(broken.id) in caplog.text
    assert "not-an-email" not in caplog.text
