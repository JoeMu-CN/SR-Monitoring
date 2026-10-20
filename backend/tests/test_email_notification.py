"""邮件渠道 Provider 单元测试（不连接真实 SMTP）。

覆盖《风险提醒邮件推送接入方案.md》批次 1 验收点：SSL / STARTTLS 两种连接、
认证失败映射、收件人校验、主题头注入防护、未启用时 enabled=False。
全部用例通过 monkeypatch 替换 smtplib 类，不产生任何外网调用。
"""

from __future__ import annotations

import smtplib
from dataclasses import replace
from email import message_from_string
from email.header import decode_header
from email.message import Message
from typing import Any

import pytest

from app.config import NotificationSettings, get_notification_settings
from app.notification.email_provider import EmailProvider
from app.notification.providers import NotificationError


def make_settings(**overrides: object) -> NotificationSettings:
    """以真实配置为底，仅覆盖邮件相关字段（与 test_notifications.py 同风格）。"""
    base = get_notification_settings()
    defaults: dict[str, object] = {
        "email_enabled": True,
        "email_smtp_host": "smtp.example.com",
        "email_smtp_port": 465,
        "email_smtp_security": "ssl",
        "email_username": "bot@example.com",
        "email_password": "secret-password",
        "email_from": "risk@example.com",
        "email_from_name": "供应商风险监控",
        "email_timeout_seconds": 20.0,
        "email_user_hourly_limit": 20,
        "email_global_hourly_limit": 60,
    }
    defaults.update(overrides)
    return replace(base, **defaults)  # type: ignore[arg-type]


class FakeSMTP:
    """记录调用序列的假 SMTP 连接（同时用于 SSL 与 STARTTLS 分支）。"""

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list[str] = []
        self.credentials: tuple[str, str] | None = None
        self.sent: list[tuple[str, list[str], str]] = []
        self.login_error: Exception | None = None

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append("login")
        if self.login_error is not None:
            raise self.login_error
        self.credentials = (username, password)

    def sendmail(self, from_addr: str, to_addrs: list[str], message: str) -> None:
        self.calls.append("sendmail")
        self.sent.append((from_addr, list(to_addrs), message))

    def quit(self) -> None:
        self.calls.append("quit")


def _install_fake(
    monkeypatch: pytest.MonkeyPatch, name: str, created: list[FakeSMTP]
) -> None:
    def factory(host: str, port: int, timeout: float | None = None) -> FakeSMTP:
        instance = FakeSMTP(host, port, timeout)
        created.append(instance)
        return instance

    monkeypatch.setattr(smtplib, name, factory)


def _decode(value: str) -> str:
    """解码 RFC2047 主题/发件人，便于断言中文内容。"""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        parts.append(chunk.decode(charset or "utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(parts)


def _parse(sent: list[tuple[str, list[str], str]]) -> Message:
    assert sent, "SMTP 未收到任何邮件"
    return message_from_string(sent[0][2])


def _assert_email_ok(instance: FakeSMTP, content: str) -> None:
    message = _parse(instance.sent)
    assert "[供应商风险]" in _decode(str(message["Subject"]))
    assert _decode(str(message["To"])) == "user@example.com"
    assert _decode(str(message["From"])).endswith("<risk@example.com>")
    assert instance.sent[0][1] == ["user@example.com"]
    body = message.get_payload(decode=True)
    assert isinstance(body, bytes) and content in body.decode("utf-8")


def test_send_via_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeSMTP] = []
    _install_fake(monkeypatch, "SMTP_SSL", created)

    provider = EmailProvider(make_settings(), recipient="user@example.com")
    provider.send("P1 风险提醒", "供应商甲新增经营异常")

    assert provider.name == "email"
    assert provider.enabled is True
    assert len(created) == 1
    instance = created[0]
    assert instance.host == "smtp.example.com"
    assert instance.port == 465
    assert instance.timeout == 20.0
    assert instance.calls == ["login", "sendmail", "quit"]
    _assert_email_ok(instance, "供应商甲新增经营异常")


def test_send_via_starttls_logs_in(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeSMTP] = []
    ssl_created: list[FakeSMTP] = []
    _install_fake(monkeypatch, "SMTP", created)
    _install_fake(monkeypatch, "SMTP_SSL", ssl_created)

    settings = make_settings(
        email_smtp_security="starttls", email_smtp_port=587
    )
    provider = EmailProvider(settings, recipient="user@example.com")
    provider.send("P2 风险提醒", "供应商乙司法诉讼")

    assert ssl_created == []
    instance = created[0]
    assert (instance.host, instance.port, instance.timeout) == (
        "smtp.example.com",
        587,
        20.0,
    )
    # 顺序即协议要求：ehlo → starttls → ehlo → login → sendmail。
    assert instance.calls == ["ehlo", "starttls", "ehlo", "login", "sendmail", "quit"]
    assert instance.credentials == ("bot@example.com", "secret-password")
    _assert_email_ok(instance, "供应商乙司法诉讼")


def test_send_without_username_skips_login(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeSMTP] = []
    _install_fake(monkeypatch, "SMTP_SSL", created)

    provider = EmailProvider(
        make_settings(email_username="", email_password=""), recipient="user@example.com"
    )
    provider.send("测试", "内网 relay 无认证")

    assert created[0].calls == ["sendmail", "quit"]


def test_auth_failure_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeSMTP] = []

    def factory(host: str, port: int, timeout: float | None = None) -> FakeSMTP:
        instance = FakeSMTP(host, port, timeout)
        instance.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
        created.append(instance)
        return instance

    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)

    provider = EmailProvider(make_settings(), recipient="user@example.com")
    with pytest.raises(NotificationError) as excinfo:
        provider.send("测试", "正文")

    message = str(excinfo.value)
    assert "SMTPAuthenticationError" in message
    assert "535" in message
    # 凭据与 SMTP 响应体都不得进入异常消息。
    assert "secret-password" not in message
    assert "bad credentials" not in message
    assert "bot@example.com" not in message
    # 认证失败后仍需关闭连接。
    assert created[0].calls[-1] == "quit"


@pytest.mark.parametrize(
    "recipient",
    ["a b@x.com", "", "user@example.com\nBcc: evil@example.com", "user", "a@b@c"],
)
def test_invalid_recipient_rejected(
    monkeypatch: pytest.MonkeyPatch, recipient: str
) -> None:
    _install_fake(monkeypatch, "SMTP_SSL", [])
    with pytest.raises(NotificationError, match="邮件收件人地址非法"):
        EmailProvider(make_settings(), recipient=recipient)


def test_subject_crlf_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeSMTP] = []
    _install_fake(monkeypatch, "SMTP_SSL", created)

    provider = EmailProvider(make_settings(), recipient="user@example.com")
    provider.send("第一行\r\nBcc: evil@example.com", "正文")

    message = _parse(created[0].sent)
    subject = _decode(str(message["Subject"]))
    # 标题里的换行被剔除：既不出现在解码后的主题里，也没有变成额外邮件头。
    assert "\r" not in subject and "\n" not in subject
    # 换行被剥离后注入文本只是普通主题文字，无法生成额外邮件头。
    assert "Bcc" in subject and "evil@example.com" in subject
    assert "Bcc" not in message


def test_disabled_when_email_switch_off() -> None:
    provider = EmailProvider(
        make_settings(email_enabled=False), recipient="user@example.com"
    )
    assert provider.enabled is False


def test_disabled_when_smtp_host_missing() -> None:
    provider = EmailProvider(
        make_settings(email_smtp_host=""), recipient="user@example.com"
    )
    assert provider.enabled is False


def test_smtp_error_is_mapped_without_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeSMTP] = []

    class _FailingSMTP(FakeSMTP):
        def sendmail(self, from_addr: str, to_addrs: Any, message: Any) -> None:
            raise smtplib.SMTPServerDisconnected("connection lost")

    def factory(host: str, port: int, timeout: float | None = None) -> FakeSMTP:
        instance = _FailingSMTP(host, port, timeout)
        created.append(instance)
        return instance

    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)

    provider = EmailProvider(make_settings(), recipient="user@example.com")
    with pytest.raises(NotificationError) as excinfo:
        provider.send("测试", "正文")

    assert str(excinfo.value) == "邮件发送失败: SMTPServerDisconnected"
    assert created[0].calls[-1] == "quit"
