"""通知模块测试：扫描编排、防骚扰、渠道 Provider、API 权限。

覆盖《风险预警手机推送接入方案.md》第 13 章验收标准的核心逻辑
（链路、去重、合并、限频、免打扰、失败重试、升级重推、权限、密钥安全）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import NotificationSettings, get_notification_settings
from app.notification.models import NotificationDelivery, NotificationSubscription
from app.notification.providers import (
    DingTalkProvider,
    NotificationError,
    NotifyProvider,
    _check_response,
    build_providers,
)
from app.notification.service import scan_and_notify
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier

# 相对当前时间的扫描基准：投递记录 created_at 由数据库 server_default 生成，
# 窗口差值须以真实当前时间为基准，不能用过去固定时刻。
T0 = datetime.now(UTC)

_LEVEL_TO_SEVERITY = {
    "P1": "critical",
    "P2": "high",
    "P3": "medium",
    "P4": "low",
}


class FakeProvider(NotifyProvider):
    """可注入的假渠道：记录调用、可模拟失败。"""

    def __init__(self, name: str = "dingtalk", *, fail: bool = False) -> None:
        super().__init__(name=name, enabled=True)
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        self.calls.append((title, content))
        if self.fail:
            raise NotificationError("fake channel failure")


def make_settings(**overrides: object) -> NotificationSettings:
    base = get_notification_settings()
    defaults: dict[str, object] = {
        "enabled": True,
        "dingtalk_enabled": True,
        "dingtalk_webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=fake",
        "merge_window_minutes": 15,
        "hourly_limit": 20,
        "retry_attempts": 2,
        "frontend_url": "",
    }
    defaults.update(overrides)
    return replace(base, **defaults)  # type: ignore[arg-type]


_id_counter = 0


def make_alert(db: Session, *, level: str = "P1") -> RiskAlert:
    global _id_counter
    _id_counter += 1
    supplier = Supplier(
        supplier_code=f"SUP-TEST-{_id_counter:06d}",
        legal_name="测试供应商有限公司",
        country_code="CN",
        enabled=True,
    )
    event = RiskEvent(
        dedup_key=f"evt-{_id_counter:06d}",
        event_type="司法风险",
        event_subtype="被执行人",
        severity=_LEVEL_TO_SEVERITY[level],
        summary="因合同纠纷被列入被执行人名单",
        confidence=0.9,
        facts={},
    )
    db.add(supplier)
    db.add(event)
    db.flush()
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="entity",
        score=95,
        reasons=["主体名称命中"],
        evidence=[],
    )
    db.add(match)
    db.flush()
    alert = RiskAlert(
        match_id=match.id,
        level=level,
        score=95,
        score_detail={"rule_version": "v1"},
        status="current",
    )
    db.add(alert)
    db.flush()
    return alert


def _delivery_for(session: Session, alert_id: int) -> NotificationDelivery | None:
    return session.scalar(
        select(NotificationDelivery).where(NotificationDelivery.alert_id == alert_id)
    )


def test_p1_sent_immediately(db_session: Session) -> None:
    """V2：新 P1 提醒 → 立即发送并落 success 投递记录。"""
    alert = make_alert(db_session, level="P1")
    provider = FakeProvider()
    summary = scan_and_notify(db_session, make_settings(), now=T0, providers=[provider])

    assert summary["new_alerts"] == 1
    assert summary["sent"] == 1
    assert len(provider.calls) == 1
    title, content = provider.calls[0]
    assert "P1" in title
    assert "测试供应商有限公司" in title
    assert "司法风险" in content
    delivery = _delivery_for(db_session, alert.id)
    assert delivery is not None
    assert delivery.status == "success"
    assert delivery.pushed_level == "P1"


def test_same_alert_not_resent(db_session: Session) -> None:
    """V3：同一事件第二轮扫描不重复推送。"""
    make_alert(db_session, level="P1")
    provider = FakeProvider()
    settings = make_settings()
    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    scan_and_notify(
        db_session, settings, now=T0 + timedelta(minutes=5), providers=[provider]
    )

    assert len(provider.calls) == 1


def test_p2_merged_after_window(db_session: Session) -> None:
    """V4：P2 提醒 15 分钟窗口内合并为一条摘要。"""
    make_alert(db_session, level="P2")
    make_alert(db_session, level="P2")
    provider = FakeProvider()
    settings = make_settings(merge_window_minutes=15)

    scan_and_notify(db_session, settings, now=T0, providers=[provider])
    assert len(provider.calls) == 0  # P2 先入队，不立即发送

    scan_and_notify(
        db_session, settings, now=T0 + timedelta(minutes=16), providers=[provider]
    )
    assert len(provider.calls) == 1
    title, _content = provider.calls[0]
    assert "共 2 条提醒" in title

    records = list(
        db_session.scalars(
            select(NotificationDelivery).order_by(NotificationDelivery.id)
        )
    )
    # 合并后：首条升级为摘要（alert_id 置空），其余标记 merged
    assert len(records) == 2
    statuses = sorted(r.status for r in records)
    assert statuses == ["merged", "success"]
    assert all(r.alert_id is None for r in records if r.status == "success")


def test_hourly_limit_defers_p1(db_session: Session) -> None:
    """V5：单小时限频超限时 P1 转合并队列，记录留痕。"""
    make_alert(db_session, level="P1")
    db_session.add(
        NotificationDelivery(
            alert_id=None,
            channel="dingtalk",
            status="success",
            title="已发送",
            content="占位",
            delivered_at=T0 - timedelta(minutes=30),
        )
    )
    db_session.commit()
    provider = FakeProvider()
    settings = make_settings(hourly_limit=1)

    summary = scan_and_notify(db_session, settings, now=T0, providers=[provider])
    assert summary["rate_limited"] == 1
    assert provider.calls == []
    delivery = db_session.scalar(
        select(NotificationDelivery).where(NotificationDelivery.status == "queued")
    )
    assert delivery is not None


def test_quiet_hours_suppress_p2_only(db_session: Session) -> None:
    """V6：免打扰时段 P2 抑制、P1 仍推。"""
    db_session.add(
        NotificationSubscription(
            channel="global",
            receiver="全局配置",
            push_levels=["P1", "P2"],
            quiet_hours={"start": "22:00", "end": "08:00"},
            enabled=True,
        )
    )
    db_session.commit()
    make_alert(db_session, level="P2")
    make_alert(db_session, level="P1")
    provider = FakeProvider()
    quiet_time = datetime(2026, 8, 24, 23, 0, tzinfo=UTC)

    summary = scan_and_notify(db_session, make_settings(), now=quiet_time, providers=[provider])
    assert summary["quiet_suppressed"] == 1
    assert summary["sent"] == 1
    assert len(provider.calls) == 1
    assert "P1" in provider.calls[0][0]


def test_failed_send_marks_failed(db_session: Session) -> None:
    """V7：发送失败按窗口重试，超过上限落 failed（不静默丢失）。"""
    alert = make_alert(db_session, level="P1")
    provider = FakeProvider(fail=True)
    settings = make_settings(retry_attempts=2)

    first = scan_and_notify(db_session, settings, now=T0, providers=[provider])
    assert first["failed"] == 0

    second = scan_and_notify(
        db_session,
        settings,
        now=T0 + timedelta(minutes=16),
        providers=[provider],
    )
    assert second["failed"] == 1
    delivery = _delivery_for(db_session, alert.id)
    assert delivery is not None
    assert delivery.status == "failed"
    assert delivery.attempt == 2
    assert "fake channel failure" in (delivery.error or "")


def test_upgrade_retriggers_push(db_session: Session) -> None:
    """等级升级（P2→P1）触发重推，并更新 pushed_level。"""
    alert = make_alert(db_session, level="P2")
    db_session.add(
        NotificationDelivery(
            alert_id=alert.id,
            channel="dingtalk",
            status="success",
            title="旧",
            content="旧",
            pushed_level="P2",
            delivered_at=T0,
        )
    )
    db_session.commit()
    alert.level = "P1"
    db_session.commit()

    provider = FakeProvider()
    summary = scan_and_notify(
        db_session, make_settings(), now=T0 + timedelta(minutes=5), providers=[provider]
    )
    assert summary["upgraded"] == 1
    assert len(provider.calls) == 1
    assert "P1" in provider.calls[0][0]
    delivery = _delivery_for(db_session, alert.id)
    assert delivery is not None
    assert delivery.pushed_level == "P1"


def test_channel_disabled_by_subscription(db_session: Session) -> None:
    """订阅表禁用渠道后不再推送（.env 已启用）。"""
    make_alert(db_session, level="P1")
    db_session.add(
        NotificationSubscription(
            channel="dingtalk", receiver="测试群", push_levels=["P1", "P2"], enabled=False
        )
    )
    db_session.commit()
    provider = FakeProvider()
    summary = scan_and_notify(db_session, make_settings(), now=T0, providers=[provider])
    assert summary["channels"] == 0
    assert provider.calls == []


def test_api_test_endpoint_requires_channel(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V1 前置：未配置任何渠道时测试端点返回 400。"""
    monkeypatch.setenv("NOTIFY_ENABLED", "true")
    monkeypatch.delenv("NOTIFY_DINGTALK_ENABLED", raising=False)
    monkeypatch.delenv("NOTIFY_DINGTALK_WEBHOOK_URL", raising=False)
    response = client.post("/api/v1/notifications/test", json={})
    assert response.status_code == 400


def test_api_permission_denied(
    client: TestClient, auth_as: Callable[[str, str], User]
) -> None:
    """V8：非 risk_admin 调用通知管理接口返回 403。"""
    auth_as("viewer", "viewer-user")
    response = client.get("/api/v1/notifications/deliveries")
    assert response.status_code == 403
    response = client.post("/api/v1/notifications/test", json={})
    assert response.status_code == 403


def test_subscription_api_roundtrip(client: TestClient) -> None:
    """订阅配置 API：查询 → 更新全局 → 渠道启停。"""
    response = client.get("/api/v1/notifications/subscriptions")
    assert response.status_code == 200
    rows = response.json()
    assert any(row["channel"] == "global" for row in rows)

    response = client.put(
        "/api/v1/notifications/subscriptions",
        json={"push_levels": ["P1"], "quiet_hours": {"start": "22:00", "end": "08:00"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["push_levels"] == ["P1"]

    response = client.put(
        "/api/v1/notifications/subscriptions/dingtalk",
        json={"enabled": False, "receiver": "测试群"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False

    response = client.put(
        "/api/v1/notifications/subscriptions/unknown",
        json={"enabled": True},
    )
    assert response.status_code == 422


def test_provider_rejects_non_whitelisted_host() -> None:
    """V9：webhook 域名白名单校验，错误消息不泄露 token。"""
    settings = make_settings(
        dingtalk_webhook_url="https://evil.example.com/robot/send?access_token=SECRETTOKEN"
    )
    provider = DingTalkProvider(settings)
    with pytest.raises(NotificationError) as exc_info:
        provider.send("标题", "正文")
    message = str(exc_info.value)
    assert "evil.example.com" in message
    assert "SECRETTOKEN" not in message


def test_check_response_parses_business_error_code() -> None:
    """钉钉/飞书业务失败时 HTTP 200 + errcode，必须识别为失败（防假成功）。"""
    import httpx

    response = httpx.Response(
        200, json={"errcode": 300005, "errmsg": "token is not exist"}
    )
    with pytest.raises(NotificationError) as exc_info:
        _check_response(response, "dingtalk")
    assert "token is not exist" in str(exc_info.value)

    ok = httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
    _check_response(ok, "dingtalk")  # 不抛异常

    pushplus_ok = httpx.Response(200, json={"code": 200, "msg": "success"})
    _check_response(pushplus_ok, "pushplus")  # 不抛异常


def test_build_providers_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """渠道按环境变量启用；未配置密钥的渠道不构建。"""
    monkeypatch.setenv("NOTIFY_DINGTALK_ENABLED", "true")
    monkeypatch.setenv(
        "NOTIFY_DINGTALK_WEBHOOK_URL",
        "https://oapi.dingtalk.com/robot/send?access_token=x",
    )
    monkeypatch.delenv("NOTIFY_FEISHU_ENABLED", raising=False)
    providers = build_providers(get_notification_settings())
    names = [provider.name for provider in providers]
    assert "dingtalk" in names
    assert "feishu" not in names
