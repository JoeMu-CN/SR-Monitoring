"""通知渠道 Provider 实现。

- 统一接口：``send(title, content)``，发送失败抛 NotificationError。
- 域名白名单：webhook URL 仅允许配置的官方域名，防配置被篡改后的 SSRF。
- 密钥不落日志：错误信息只保留 HTTP 状态与摘要，不打印 URL 查询串中的 token。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from app.config import NotificationSettings

# 允许外发的推送域名白名单（SSRF 防护：webhook URL 域名必须命中）
ALLOWED_NOTIFY_HOSTS = frozenset(
    {
        "oapi.dingtalk.com",
        "open.feishu.cn",
        "sctapi.ftqq.com",
        "pushplus.plus",
        "www.pushplus.plus",
    }
)


class NotificationError(RuntimeError):
    """渠道发送失败（不含密钥信息）。"""


@dataclass(frozen=True)
class NotifyProvider:
    """通知渠道实现。"""

    name: str
    enabled: bool

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        raise NotImplementedError


def _validate_host(url: str) -> None:
    hostname = (urlparse(url).hostname or "").rstrip(".").lower()
    if hostname not in ALLOWED_NOTIFY_HOSTS:
        raise NotificationError(f"通知地址域名不在白名单内: {hostname or '(空)'}")
    if urlparse(url).scheme not in {"https", "http"}:
        raise NotificationError("通知地址必须为 http(s) 协议")


def _post_json(url: str, payload: dict[str, object], *, timeout: float) -> httpx.Response:
    _validate_host(url)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise NotificationError(f"通知请求失败: {type(exc).__name__}") from exc
    return response


def _post_form(url: str, data: dict[str, str], *, timeout: float) -> httpx.Response:
    _validate_host(url)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(url, data=data)
    except httpx.HTTPError as exc:
        raise NotificationError(f"通知请求失败: {type(exc).__name__}") from exc
    return response


def _dingtalk_sign(timestamp_ms: int, secret: str) -> str:
    """钉钉加签：HmacSHA256(secret, timestamp+"\\n"+secret) → Base64 → URL 编码。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    return quote(base64.b64encode(digest).decode("utf-8"))


def _feishu_sign(timestamp_sec: int, secret: str) -> str:
    """飞书加签：HmacSHA256(key=secret, timestamp+"\\n"+secret) → Base64。"""
    string_to_sign = f"{timestamp_sec}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


class DingTalkProvider(NotifyProvider):
    def __init__(self, settings: NotificationSettings) -> None:
        super().__init__(
            name="dingtalk",
            enabled=(
                settings.dingtalk_enabled and bool(settings.dingtalk_webhook_url)
            ),
        )
        self._webhook_url = settings.dingtalk_webhook_url
        self._secret = settings.dingtalk_secret
        self._timeout = settings.timeout_seconds

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        url = self._webhook_url
        timestamp_ms = int(time.time() * 1000)
        if self._secret:
            url = (
                f"{url}{'&' if '?' in url else '?'}"
                f"timestamp={timestamp_ms}&sign={_dingtalk_sign(timestamp_ms, self._secret)}"
            )
        response = _post_json(
            url,
            {"msgtype": "text", "text": {"content": f"{title}\n{content}"}},
            timeout=timeout or self._timeout,
        )
        _check_response(response, self.name)


class FeishuProvider(NotifyProvider):
    def __init__(self, settings: NotificationSettings) -> None:
        super().__init__(
            name="feishu",
            enabled=settings.feishu_enabled and bool(settings.feishu_webhook_url),
        )
        self._webhook_url = settings.feishu_webhook_url
        self._secret = settings.feishu_secret
        self._timeout = settings.timeout_seconds

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        timestamp_sec = int(time.time())
        payload: dict[str, object] = {
            "msg_type": "text",
            "content": {"text": f"{title}\n{content}"},
        }
        if self._secret:
            payload["timestamp"] = str(timestamp_sec)
            payload["sign"] = _feishu_sign(timestamp_sec, self._secret)
        response = _post_json(
            self._webhook_url, payload, timeout=timeout or self._timeout
        )
        _check_response(response, self.name)


class ServerChanProvider(NotifyProvider):
    def __init__(self, settings: NotificationSettings) -> None:
        super().__init__(
            name="serverchan",
            enabled=settings.serverchan_enabled and bool(settings.serverchan_send_key),
        )
        self._send_key = settings.serverchan_send_key
        self._timeout = settings.timeout_seconds

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        url = f"https://sctapi.ftqq.com/{self._send_key}.send"
        response = _post_form(
            url,
            {"title": title, "desp": content},
            timeout=timeout or self._timeout,
        )
        _check_response(response, self.name)


class PushPlusProvider(NotifyProvider):
    def __init__(self, settings: NotificationSettings) -> None:
        super().__init__(
            name="pushplus",
            enabled=settings.pushplus_enabled and bool(settings.pushplus_token),
        )
        self._token = settings.pushplus_token
        self._timeout = settings.timeout_seconds

    def send(self, title: str, content: str, *, timeout: float | None = None) -> None:
        response = _post_json(
            "https://www.pushplus.plus/send",
            {
                "token": self._token,
                "title": title,
                "content": content,
                "template": "txt",
            },
            timeout=timeout or self._timeout,
        )
        _check_response(response, self.name)


def _check_response(response: httpx.Response, channel: str) -> None:
    """校验渠道响应；只保留错误摘要，不落 URL/密钥。

    注意：钉钉/飞书/PushPlus 在业务失败时仍返回 HTTP 200，真实结果在
    响应体错误码中（钉钉 errcode、飞书 code、PushPlus code）。
    仅看 HTTP 状态会"假成功"，必须解析错误码。
    """
    if response.status_code < 200 or response.status_code >= 300:
        raise NotificationError(
            f"{channel} 渠道返回 HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        body = response.json()
    except ValueError:
        return  # 非 JSON 响应（如 Server酱成功）且 HTTP 2xx，视为成功
    if not isinstance(body, dict):
        return
    error_code = body.get("errcode", body.get("code"))
    if error_code is not None and error_code not in (0, 200, "0", "200"):
        message = body.get("errmsg") or body.get("msg") or f"错误码 {error_code}"
        raise NotificationError(f"{channel} 渠道发送失败: {message}")
    if body.get("success") is False or body.get("ok") is False:
        raise NotificationError(
            f"{channel} 渠道发送失败: {body.get('errmsg') or body.get('msg') or '未知原因'}"
        )


def build_providers(settings: NotificationSettings) -> list[NotifyProvider]:
    """按配置构建启用渠道（顺序即发送优先级）。"""
    providers = [
        DingTalkProvider(settings),
        FeishuProvider(settings),
        ServerChanProvider(settings),
        PushPlusProvider(settings),
    ]
    return [provider for provider in providers if provider.enabled]
