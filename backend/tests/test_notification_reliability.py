"""通知可靠性回归（任务9）。

锁定以下可靠性语义（根因见 .omo 调试日志 H1/H2/H3）：
- notify_job 成功后显式 commit，异常显式 rollback；发送后提交失败存在
  at-least-once 重复窗口，下一成功轮收敛（不宣称 exactly-once）。
- 去重逐 (alert_id, channel) 判定，无全局游标：大 alert_id 的已有投递
  不阻塞其他渠道或更小 alert_id 的首次投递。
- success/merged 视为已送达：同级或降级不重发，等级升级可重发；
  failed 为终态；quiet_suppressed/rate_limited/expired_suppressed 在
  条件解除后复查并恢复投递。
- 摘要成功：首条 success、其余 merged，全部成员保留 alert_id/channel，
  统一 delivered_at/title/content，pushed_level 保持成员自身等级；
  失败全批一致 attempt 与线性退避，达上限全批 failed，不改写 created_at。
- 详情链接固定 frontend_url.rstrip('/') + '/risks/{id}'，摘要每成员
  含供应商标识与各自路径。
- 历史漏投只计 backfill_pending（不调用 Provider）；legacy
  alert_id IS NULL 只计数。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from test_notifications import FakeProvider, make_alert, make_settings

import app.notification.service as service_module
from app.database import engine
from app.notification.models import NotificationDelivery, NotificationSubscription
from app.notification.providers import NotifyProvider
from app.notification.service import notify_job, scan_and_notify
from app.risks.models import RiskAlert
from app.risks.query_validity import current_alert_condition

T0 = datetime.now(UTC)


def _delivery_for(session: Session, alert_id: int) -> NotificationDelivery | None:
    return session.scalar(
        select(NotificationDelivery).where(NotificationDelivery.alert_id == alert_id)
    )


def _queued_window(session: Session, *alert_ids: int) -> datetime:
    created = list(
        session.scalars(
            select(NotificationDelivery.created_at).where(
                NotificationDelivery.alert_id.in_(alert_ids)
            )
        )
    )
    return max(created) + timedelta(minutes=16)


# ---------------------------------------------------------------------------
# notify_job 持久化 / rollback / commit 失败窗口（真实 job + 独立 Session）
# ---------------------------------------------------------------------------
_NOTIFY_WIPE_TABLES = (
    "notification_deliveries",
    "notification_subscriptions",
    "risk_alerts",
    "supplier_event_matches",
    "risk_event_signals",
    "risk_events",
    "suppliers",
)


def _wipe_committed() -> None:
    with engine.begin() as connection:
        for table in _NOTIFY_WIPE_TABLES:
            connection.execute(text(f"DELETE FROM {table}"))


def _seed_committed_alert(level: str = "P1") -> int:
    with Session(bind=engine) as session:
        alert = make_alert(session, level=level)
        session.commit()
        return alert.id


def _patch_notify_env(
    monkeypatch: pytest.MonkeyPatch, providers: list[NotifyProvider]
) -> None:
    settings = make_settings(frontend_url="https://risk.example.com")
    monkeypatch.setattr(service_module, "get_notification_settings", lambda: settings)
    monkeypatch.setattr(service_module, "build_providers", lambda _settings: providers)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(bind=engine))


def test_notify_job_persists_deliveries_visible_to_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 notify_job 发送并提交后，新 Session 必须看到投递记录（H1）。"""
    _wipe_committed()
    try:
        alert_id = _seed_committed_alert(level="P1")
        provider = FakeProvider()
        _patch_notify_env(monkeypatch, [provider])

        notify_job()

        with Session(bind=engine) as session:
            rows = list(session.scalars(select(NotificationDelivery)))
        assert len(rows) == 1
        assert rows[0].alert_id == alert_id
        assert rows[0].status == "success"
        assert len(provider.calls) == 1

        notify_job()
        with Session(bind=engine) as session:
            rows = list(session.scalars(select(NotificationDelivery)))
        assert len(rows) == 1
        assert len(provider.calls) == 1  # 第二轮不重发
    finally:
        _wipe_committed()


def test_notify_job_rolls_back_on_scan_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描中途异常 → 显式 rollback，新 Session 不留任何投递记录。"""
    _wipe_committed()
    try:
        _seed_committed_alert(level="P2")
        provider = FakeProvider()
        _patch_notify_env(monkeypatch, [provider])

        def _explode(*args: object, **kwargs: object) -> dict[str, int]:
            raise RuntimeError("simulated scan crash")

        monkeypatch.setattr(service_module, "_process_merge_queue", _explode)
        notify_job()  # 异常被 job 捕获，不得向外抛出

        with Session(bind=engine) as session:
            rows = list(session.scalars(select(NotificationDelivery)))
        assert rows == []
        assert provider.calls == []

        # 锁已释放：修复扫描后下一轮可正常执行
        monkeypatch.setattr(
            service_module, "_process_merge_queue", lambda *a, **k: None
        )
        notify_job()
        with Session(bind=engine) as session:
            rows = list(session.scalars(select(NotificationDelivery)))
        assert len(rows) == 1
    finally:
        _wipe_committed()


def test_commit_failure_window_is_at_least_once_and_converges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发送后 commit 失败 → 本轮丢失、下一成功轮重发收敛、第三轮不重发。"""
    _wipe_committed()
    try:
        alert_id = _seed_committed_alert(level="P1")
        provider = FakeProvider()
        _patch_notify_env(monkeypatch, [provider])

        class FlakyCommitSession(Session):
            commit_calls = 0

            def commit(self) -> None:
                FlakyCommitSession.commit_calls += 1
                if FlakyCommitSession.commit_calls == 1:
                    raise RuntimeError("simulated commit failure")
                super().commit()

        monkeypatch.setattr(
            service_module, "SessionLocal", sessionmaker(bind=engine, class_=FlakyCommitSession)
        )

        notify_job()  # 发送已发生，但 commit 失败 → 本轮工作丢失
        with Session(bind=engine) as session:
            assert list(session.scalars(select(NotificationDelivery))) == []
        assert len(provider.calls) == 1

        notify_job()  # 下一成功轮重发并收敛（重复窗口证据）
        with Session(bind=engine) as session:
            rows = list(session.scalars(select(NotificationDelivery)))
        assert len(rows) == 1
        assert rows[0].status == "success"
        assert len(provider.calls) == 2

        notify_job()  # 第三轮不再重发
        with Session(bind=engine) as session:
            rows = list(session.scalars(select(NotificationDelivery)))
        assert len(rows) == 1
        assert len(provider.calls) == 2
        assert rows[0].alert_id == alert_id
    finally:
        _wipe_committed()


# ---------------------------------------------------------------------------
# 逐 (alert_id, channel) 去重
# ---------------------------------------------------------------------------
def test_dual_channels_deliver_independently(db_session: Session) -> None:
    """双渠道各自投递同一 alert，互不影响。"""
    alert = make_alert(db_session, level="P1")
    dingtalk = FakeProvider("dingtalk")
    feishu = FakeProvider("feishu")
    summary = scan_and_notify(
        db_session, make_settings(), now=T0, providers=[dingtalk, feishu]
    )
    assert summary["sent"] == 2
    assert len(dingtalk.calls) == 1
    assert len(feishu.calls) == 1
    rows = list(
        db_session.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.alert_id == alert.id
            )
        )
    )
    assert {row.channel for row in rows} == {"dingtalk", "feishu"}
    assert all(row.status == "success" for row in rows)


def test_small_alert_id_not_blocked_by_larger_delivery(db_session: Session) -> None:
    """大 alert_id 已有投递不得阻塞更小 alert_id 对其他渠道的首次投递（H2）。"""
    small_alert = make_alert(db_session, level="P1")
    big_alert = make_alert(db_session, level="P1", alert_id=small_alert.id + 1000)
    db_session.add(
        NotificationDelivery(
            alert_id=big_alert.id,
            channel="feishu",
            status="success",
            title="旧",
            content="旧",
            pushed_level="P1",
            delivered_at=T0,
        )
    )
    db_session.flush()

    dingtalk = FakeProvider("dingtalk")
    scan_and_notify(db_session, make_settings(), now=T0, providers=[dingtalk])

    assert len(dingtalk.calls) == 2  # 两个候选均对 dingtalk 首投
    small_delivery = _delivery_for(db_session, small_alert.id)
    assert small_delivery is not None
    assert small_delivery.channel == "dingtalk"
    assert small_delivery.status == "success"


# ---------------------------------------------------------------------------
# 摘要成员同步与失败一致性
# ---------------------------------------------------------------------------
def test_digest_keeps_member_links_and_syncs_state(db_session: Session) -> None:
    """摘要成功：首条 success、其余 merged，成员保留 alert_id 并同步状态。"""
    first_alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    second_alert = make_alert(db_session, level="P2", legal_name="乙供应商")
    provider = FakeProvider()
    settings = make_settings(frontend_url="https://risk.example.com")

    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    second_now = _queued_window(db_session, first_alert.id, second_alert.id)
    scan_and_notify(db_session, settings, now=second_now, providers=[provider])

    assert len(provider.calls) == 1
    records = list(
        db_session.scalars(
            select(NotificationDelivery).order_by(NotificationDelivery.id)
        )
    )
    assert len(records) == 2
    assert all(record.alert_id is not None for record in records)
    assert all(record.channel == provider.name for record in records)
    statuses = sorted(record.status for record in records)
    assert statuses == ["merged", "success"]
    delivered_at = {record.delivered_at for record in records}
    assert len(delivered_at) == 1 and None not in delivered_at
    titles = {record.title for record in records}
    contents = {record.content for record in records}
    assert len(titles) == 1 and len(contents) == 1
    assert "共 2 条提醒" in next(iter(titles))
    for record in records:
        assert record.pushed_level == "P2"

    scan_and_notify(
        db_session,
        settings,
        now=second_now + timedelta(minutes=5),
        providers=[provider],
    )
    assert len(provider.calls) == 1  # 连续扫描不重发


def test_digest_members_each_have_supplier_and_own_link(db_session: Session) -> None:
    """摘要正文每成员含供应商标识与各自 /risks/{id} 路径。"""
    first_alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    second_alert = make_alert(db_session, level="P2", legal_name="乙供应商")
    provider = FakeProvider()
    settings = make_settings(frontend_url="https://risk.example.com")

    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    second_now = _queued_window(db_session, first_alert.id, second_alert.id)
    scan_and_notify(db_session, settings, now=second_now, providers=[provider])

    _title, content = provider.calls[0]
    assert f"https://risk.example.com/risks/{first_alert.id}" in content
    assert f"https://risk.example.com/risks/{second_alert.id}" in content
    assert "甲供应商" in content
    assert "乙供应商" in content
    assert "/#/risk-alerts/" not in content


def test_digest_failure_all_members_consistent_and_bounded(db_session: Session) -> None:
    """摘要失败：全批一致 attempt、线性退避、达上限全批 failed、不改 created_at。"""
    first_alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    second_alert = make_alert(db_session, level="P2", legal_name="乙供应商")
    provider = FakeProvider(fail=True)
    settings = make_settings(retry_attempts=2)

    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    second_now = _queued_window(db_session, first_alert.id, second_alert.id)
    created_at = {
        d.id: d.created_at
        for d in db_session.scalars(select(NotificationDelivery))
    }

    scan_and_notify(db_session, settings, now=second_now, providers=[provider])
    records = list(db_session.scalars(select(NotificationDelivery)))
    assert len(provider.calls) == 1
    assert {record.attempt for record in records} == {1}
    assert all(record.status == "queued" for record in records)

    # 线性退避：attempt=1 后 15 分钟内不重试
    scan_and_notify(
        db_session, settings, now=second_now + timedelta(minutes=1), providers=[provider]
    )
    assert len(provider.calls) == 1

    scan_and_notify(
        db_session, settings, now=second_now + timedelta(minutes=16), providers=[provider]
    )
    records = list(
        db_session.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id))
    )
    assert len(provider.calls) == 2
    assert {record.status for record in records} == {"failed"}
    assert {record.attempt for record in records} == {2}
    assert all("fake channel failure" in (record.error or "") for record in records)
    assert all(record.created_at == created_at[record.id] for record in records)

    scan_and_notify(
        db_session,
        settings,
        now=second_now + timedelta(minutes=32),
        providers=[provider],
    )
    assert len(provider.calls) == 2  # failed 终态不再重试


def test_upgrade_after_merged_digest_resends(db_session: Session) -> None:
    """摘要成员（merged）在等级升级后可重发并更新 pushed_level。"""
    alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    provider = FakeProvider()
    settings = make_settings(frontend_url="https://risk.example.com")

    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    scan_and_notify(
        db_session, settings, now=_queued_window(db_session, alert.id), providers=[provider]
    )
    assert len(provider.calls) == 1

    alert.level = "P1"
    db_session.flush()
    summary = scan_and_notify(
        db_session, settings, now=T0 + timedelta(hours=2), providers=[provider]
    )
    assert summary["upgraded"] == 1
    assert len(provider.calls) == 2
    assert "P1" in provider.calls[1][0]
    delivery = _delivery_for(db_session, alert.id)
    assert delivery is not None
    assert delivery.pushed_level == "P1"
    assert delivery.status == "success"


# ---------------------------------------------------------------------------
# quiet / rate 恢复
# ---------------------------------------------------------------------------
def test_quiet_suppressed_recovered_after_quiet_hours(db_session: Session) -> None:
    """免打扰抑制的记录在时段解除后复查并恢复投递。"""
    alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    scan_now = T0 + timedelta(minutes=16)
    # 免打扰窗口整体放在扫描时刻之后，保证恢复扫描时抑制条件已解除
    quiet_hours = {
        "start": (scan_now + timedelta(hours=1)).strftime("%H:%M"),
        "end": (scan_now + timedelta(hours=2)).strftime("%H:%M"),
    }
    db_session.add(
        NotificationSubscription(
            channel="global",
            receiver="全局配置",
            push_levels=["P1", "P2"],
            quiet_hours=quiet_hours,
            enabled=True,
        )
    )
    db_session.add(
        NotificationDelivery(
            alert_id=alert.id,
            channel="dingtalk",
            status="quiet_suppressed",
            title="旧",
            content="旧",
            pushed_level="P2",
            created_at=T0 - timedelta(hours=2),
        )
    )
    db_session.flush()

    provider = FakeProvider()
    scan_and_notify(db_session, make_settings(), now=scan_now, providers=[provider])

    assert len(provider.calls) == 1
    delivery = _delivery_for(db_session, alert.id)
    assert delivery is not None
    assert delivery.status == "success"
    assert delivery.delivered_at is not None


def test_rate_limited_recovered_when_capacity_available(db_session: Session) -> None:
    """限频抑制的记录在容量恢复后复查并恢复投递。"""
    alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    db_session.add(
        NotificationDelivery(
            alert_id=alert.id,
            channel="dingtalk",
            status="rate_limited",
            title="旧",
            content="旧",
            pushed_level="P2",
            created_at=T0 - timedelta(hours=2),
        )
    )
    db_session.flush()

    provider = FakeProvider()
    scan_and_notify(
        db_session, make_settings(hourly_limit=20), now=T0, providers=[provider]
    )

    assert len(provider.calls) == 1
    delivery = _delivery_for(db_session, alert.id)
    assert delivery is not None
    assert delivery.status == "success"


def test_revoked_before_digest_send_is_suppressed(db_session: Session) -> None:
    """发送前撤销：撤销成员被抑制，不进摘要。"""
    first_alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    second_alert = make_alert(db_session, level="P2", legal_name="乙供应商")
    provider = FakeProvider()
    settings = make_settings(frontend_url="https://risk.example.com")

    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    first_alert.status = "expired"
    db_session.flush()

    scan_and_notify(
        db_session,
        settings,
        now=_queued_window(db_session, first_alert.id, second_alert.id),
        providers=[provider],
    )

    assert len(provider.calls) == 1
    assert "共 1 条提醒" in provider.calls[0][0]
    first_delivery = _delivery_for(db_session, first_alert.id)
    assert first_delivery is not None
    assert first_delivery.status == "expired_suppressed"
    second_delivery = _delivery_for(db_session, second_alert.id)
    assert second_delivery is not None
    assert second_delivery.status == "success"
    assert second_delivery.alert_id == second_alert.id


# ---------------------------------------------------------------------------
# 链接 / backfill / legacy
# ---------------------------------------------------------------------------
def test_single_alert_detail_link_format(db_session: Session) -> None:
    """单条提醒链接固定 frontend_url + /risks/{id}。"""
    alert = make_alert(db_session, level="P1", legal_name="甲供应商")
    provider = FakeProvider()
    scan_and_notify(
        db_session,
        make_settings(frontend_url="https://risk.example.com/"),
        now=T0,
        providers=[provider],
    )
    _title, content = provider.calls[0]
    assert f"https://risk.example.com/risks/{alert.id}" in content
    assert "/#/risk-alerts/" not in content


def test_backfill_pending_reported_not_sent(db_session: Session) -> None:
    """历史漏投（无投递记录且创建超过基线窗口）只计数，不补发。"""
    stale_alert = make_alert(db_session, level="P1", legal_name="陈旧供应商")
    stale_alert.created_at = T0 - timedelta(days=2)
    fresh_alert = make_alert(db_session, level="P1", legal_name="新鲜供应商")
    db_session.flush()

    provider = FakeProvider()
    summary = scan_and_notify(
        db_session, make_settings(), now=T0, providers=[provider]
    )

    assert summary["backfill_pending"] == 1
    assert summary["sent"] == 1
    assert len(provider.calls) == 1
    assert "P1" in provider.calls[0][0]
    assert _delivery_for(db_session, stale_alert.id) is None
    assert _delivery_for(db_session, fresh_alert.id) is not None


def test_legacy_unlinked_rows_only_counted(db_session: Session) -> None:
    """legacy alert_id IS NULL 记录只计数报告，不影响新提醒投递。"""
    alert = make_alert(db_session, level="P1", legal_name="甲供应商")
    db_session.add(
        NotificationDelivery(
            alert_id=None,
            channel="dingtalk",
            status="success",
            title="【风险预警汇总】旧摘要",
            content="旧",
            delivered_at=T0 - timedelta(hours=1),
        )
    )
    db_session.flush()

    provider = FakeProvider()
    summary = scan_and_notify(db_session, make_settings(), now=T0, providers=[provider])

    assert summary["legacy_unlinked"] == 1
    assert summary["sent"] == 1
    assert len(provider.calls) == 1
    assert _delivery_for(db_session, alert.id) is not None


def test_current_alert_condition_validity_still_enforced(db_session: Session) -> None:
    """恢复/重推路径同样先复查 current 有效性（存在性回归锁）。"""
    alert = make_alert(db_session, level="P2", legal_name="甲供应商")
    db_session.add(
        NotificationDelivery(
            alert_id=alert.id,
            channel="dingtalk",
            status="rate_limited",
            title="旧",
            content="旧",
            pushed_level="P2",
            created_at=T0 - timedelta(hours=2),
        )
    )
    alert.status = "expired"
    db_session.flush()
    assert (
        db_session.scalar(
            select(RiskAlert.id).where(
                RiskAlert.id == alert.id, current_alert_condition(T0)
            )
        )
        is None
    )

    provider = FakeProvider()
    scan_and_notify(db_session, make_settings(), now=T0, providers=[provider])
    assert provider.calls == []
