"""邮件通知渠道：基于 smtplib + email 标准库实现。

设计要点：
- **零新增依赖**：只用 Python 标准库（smtplib / email），不引入第三方邮件 SDK。
- **凭据不落日志**：异常统一转 NotificationError，消息只含异常类型名与
  ``SMTPResponseException.smtp_code``，绝不拼接用户名、密码或完整 SMTP 响应体。
- **头注入防护**：主题与收件地址先剔除 CR/LF，收件地址再做基础格式校验。
- **一信一连接**：构造时绑定单个收件人，send 签名与既有 Provider 一致，
  service 层无需区分渠道；用户规模扩大后再考虑连接复用。
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.config import NotificationSettings
from app.notification.providers import NotificationError, NotifyProvider

SUBJECT_PREFIX = "[供应商风险] "
# 与设计文档一致：465 走隐式 SSL，587 走 STARTTLS；不支持 25 明文端口。
STARTTLS_SECURITY = "starttls"


def _strip_crlf(value: str) -> str:
    """剔除回车换行并去首尾空白，防止邮件头注入。"""
    return value.replace("\r", "").replace("\n", "").strip()


def _validate_recipient(recipient: str) -> str:
    """收件地址基础校验；非法地址在构造期就失败，避免进入投递循环才报错。"""
    # 收件人是配置/数据库输入，出现 CR/LF 一律视为注入尝试直接拒绝，
    # 不做静默"清洗后照发"（主题允许清洗，地址不允许）。
    if "\r" in recipient or "\n" in recipient:
        raise NotificationError("邮件收件人地址非法")
    cleaned = recipient.strip()
    # 只接受单个地址：逗号/分号会让 sendmail 变成多收件人，超出调用方语义。
    if (
        cleaned.count("@") != 1
        or any(character.isspace() for character in cleaned)
        or "," in cleaned
        or ";" in cleaned
    ):
        raise NotificationError("邮件收件人地址非法")
    local_part, _, domain = cleaned.rpartition("@")
    if not local_part or not domain:
        raise NotificationError("邮件收件人地址非法")
    return cleaned


def _describe_failure(exc: BaseException) -> str:
    """只暴露异常类型与 SMTP 返回码，防止凭据/响应体进入投递记录。"""
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"邮件发送失败: {type(exc).__name__} (SMTP {exc.smtp_code})"
    return f"邮件发送失败: {type(exc).__name__}"


def _quit_quietly(connection: smtplib.SMTP | None) -> None:
    """关闭连接；quit 失败不影响已判定的发送结果。"""
    if connection is None:
        return
    try:
        connection.quit()
    except (smtplib.SMTPException, OSError, TimeoutError):
        pass


class EmailProvider(NotifyProvider):
    """单收件人邮件渠道：每个用户一个实例。"""

    def __init__(self, settings: NotificationSettings, *, recipient: str) -> None:
        cleaned = _validate_recipient(recipient)
        super().__init__(
            name="email",
            enabled=(
                settings.email_enabled
                and bool(settings.email_smtp_host)
                and bool(settings.email_from)
                and bool(cleaned)
            ),
        )
        self._host = settings.email_smtp_host
        self._port = settings.email_smtp_port
        self._security = settings.email_smtp_security
        self._username = settings.email_username
        self._password = settings.email_password
        self._from_addr = settings.email_from
        self._from_name = settings.email_from_name
        self._recipient = cleaned
        self._timeout = settings.email_timeout_seconds

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        message = EmailMessage()
        # EmailMessage 负责中文主题的 RFC2047 编码与 UTF-8 正文声明。
        message["Subject"] = f"{SUBJECT_PREFIX}{_strip_crlf(title)}"
        message["From"] = formataddr((self._from_name, self._from_addr))
        message["To"] = self._recipient
        message.set_content(content)

        connection: smtplib.SMTP | None = None
        try:
            connection = self._open_connection(timeout or self._timeout)
            if self._username:
                connection.login(self._username, self._password)
            connection.sendmail(
                self._from_addr, [self._recipient], message.as_string()
            )
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise NotificationError(_describe_failure(exc)) from exc
        finally:
            _quit_quietly(connection)

    def _open_connection(self, timeout: float) -> smtplib.SMTP:
        """建立并握手连接；starttls 分支在返回前完成 ehlo → starttls → ehlo。"""
        if self._security != STARTTLS_SECURITY:
            return smtplib.SMTP_SSL(self._host, self._port, timeout=timeout)
        connection = smtplib.SMTP(self._host, self._port, timeout=timeout)
        try:
            connection.ehlo()
            connection.starttls()
            connection.ehlo()
        except (smtplib.SMTPException, OSError, TimeoutError):
            _quit_quietly(connection)
            raise
        return connection
