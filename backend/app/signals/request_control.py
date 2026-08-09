"""第三方数据源的域名级节流、熔断和冷却。"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.signals.models import SourceHostAccess

MIN_REQUEST_INTERVAL = timedelta(seconds=10)
LEASE_DURATION = timedelta(seconds=45)
ACCESS_BLOCK_COOLDOWN = timedelta(minutes=30)
DEFAULT_RATE_LIMIT_COOLDOWN = timedelta(minutes=15)
MAX_TRANSIENT_COOLDOWN = timedelta(minutes=30)
_WAF_MARKERS = (
    "创宇盾提示您",
    "可疑的攻击行为",
    "cf-chl-",
    "cf-turnstile",
)


@dataclass(frozen=True)
class HostLease:
    hostname: str
    lease_id: str


class SourceAccessDeferred(RuntimeError):
    def __init__(self, hostname: str, until: datetime, reason: str) -> None:
        self.hostname = hostname
        self.until = until
        self.reason = reason
        super().__init__(f"数据源域名 {hostname} {reason}，请在 {until.isoformat()} 后重试")


@dataclass(frozen=True)
class ResponseDecision:
    error_kind: str | None
    message: str | None
    cooldown: timedelta | None


@dataclass(frozen=True)
class ControlledResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


class SourceRequestFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        status_code: int | None = None,
    ) -> None:
        self.error_kind = error_kind
        self.status_code = status_code
        super().__init__(message)


def acquire_host_lease(url: str, *, now: datetime | None = None) -> HostLease:
    hostname = _hostname(url)
    current = now or datetime.now(UTC)
    lease_id = uuid.uuid4().hex
    with SessionLocal() as session:
        session.execute(
            insert(SourceHostAccess)
            .values(hostname=hostname)
            .on_conflict_do_nothing(index_elements=[SourceHostAccess.hostname])
        )
        session.commit()
        state = session.get(SourceHostAccess, hostname, with_for_update=True)
        assert state is not None
        if state.cooldown_until and state.cooldown_until > current:
            raise SourceAccessDeferred(hostname, state.cooldown_until, "处于访问冷却期")
        if state.lease_until and state.lease_until > current:
            raise SourceAccessDeferred(hostname, state.lease_until, "已有请求正在执行")
        if state.next_request_at and state.next_request_at > current:
            raise SourceAccessDeferred(hostname, state.next_request_at, "请求过于频繁")
        state.lease_id = lease_id
        state.lease_until = current + LEASE_DURATION
        state.next_request_at = current + MIN_REQUEST_INTERVAL
        session.commit()
    return HostLease(hostname=hostname, lease_id=lease_id)


async def controlled_get(
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 15,
    maximum_bytes: int,
    follow_redirects: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ControlledResponse:
    """执行一次受控 GET；MockTransport 测试不访问数据库风控状态。"""
    lease: HostLease | None = None
    if transport is None:
        try:
            lease = acquire_host_lease(url)
        except SourceAccessDeferred as exc:
            raise SourceRequestFailed(
                str(exc), error_kind="deferred"
            ) from exc
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=follow_redirects,
            trust_env=False,
        ) as client:
            async with client.stream("GET", url, params=params, headers=headers) as response:
                body = await _read_limited(response, maximum_bytes)
                response_headers = dict(response.headers)
                if 300 <= response.status_code < 400 and not follow_redirects:
                    if lease:
                        release_host_lease(lease)
                    raise SourceRequestFailed(
                        "数据源返回重定向；请配置最终官方地址",
                        error_kind="redirect",
                        status_code=response.status_code,
                    )
                decision = classify_response(
                    response.status_code,
                    response_headers,
                    body.decode("utf-8", errors="replace"),
                )
                if lease:
                    complete_host_lease(
                        lease,
                        status_code=response.status_code,
                        decision=decision,
                    )
                if decision.error_kind:
                    raise SourceRequestFailed(
                        decision.message or "源站拒绝请求",
                        error_kind=decision.error_kind,
                        status_code=response.status_code,
                    )
                return ControlledResponse(response.status_code, response_headers, body)
    except SourceRequestFailed:
        if lease:
            release_host_lease(lease)
        raise
    except httpx.HTTPError as exc:
        if lease:
            complete_host_lease(lease, network_error="数据源网络请求失败")
        raise SourceRequestFailed(
            "数据源网络请求失败", error_kind="network_error"
        ) from exc
    except Exception:
        if lease:
            release_host_lease(lease)
        raise


def classify_response(
    status_code: int,
    headers: dict[str, str] | None = None,
    body_excerpt: str = "",
    *,
    now: datetime | None = None,
) -> ResponseDecision:
    lowered = body_excerpt[:32768].lower()
    waf = any(marker.lower() in lowered for marker in _WAF_MARKERS)
    if waf or status_code == 403:
        return ResponseDecision(
            "access_blocked",
            "源站拒绝访问或触发安全防护；平台已停止请求并进入冷却期",
            ACCESS_BLOCK_COOLDOWN,
        )
    if status_code == 429:
        cooldown = _retry_after(headers or {}, now=now) or DEFAULT_RATE_LIMIT_COOLDOWN
        return ResponseDecision("rate_limited", "源站请求限流", cooldown)
    if status_code in {401, 407}:
        return ResponseDecision("authentication_required", "源站要求认证", ACCESS_BLOCK_COOLDOWN)
    if status_code >= 500:
        return ResponseDecision("upstream_error", f"源站返回 HTTP {status_code}", None)
    if status_code >= 400:
        return ResponseDecision("http_error", f"源站返回 HTTP {status_code}", timedelta(minutes=5))
    return ResponseDecision(None, None, None)


def complete_host_lease(
    lease: HostLease,
    *,
    status_code: int | None = None,
    decision: ResponseDecision | None = None,
    network_error: str | None = None,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    with SessionLocal() as session:
        state = session.get(SourceHostAccess, lease.hostname, with_for_update=True)
        if state is None or state.lease_id != lease.lease_id:
            return
        state.lease_id = None
        state.lease_until = None
        state.last_http_status = status_code
        if decision and decision.error_kind:
            state.consecutive_failures += 1
            state.last_error_kind = decision.error_kind
            state.last_error = decision.message
            cooldown = decision.cooldown or _transient_cooldown(state.consecutive_failures)
            state.cooldown_until = current + cooldown
        elif network_error:
            state.consecutive_failures += 1
            state.last_error_kind = "network_error"
            state.last_error = network_error[:500]
            state.cooldown_until = current + _transient_cooldown(state.consecutive_failures)
        else:
            state.consecutive_failures = 0
            state.cooldown_until = None
            state.last_error_kind = None
            state.last_error = None
        session.commit()


def release_host_lease(lease: HostLease) -> None:
    """解析失败等非网络问题只释放租约，不改变源站健康状态。"""
    with SessionLocal() as session:
        state = session.get(SourceHostAccess, lease.hostname, with_for_update=True)
        if state is None or state.lease_id != lease.lease_id:
            return
        state.lease_id = None
        state.lease_until = None
        session.commit()


def _hostname(url: str) -> str:
    hostname = (urlparse(url).hostname or "").rstrip(".").lower()
    if not hostname:
        raise ValueError("数据源 URL 缺少有效域名")
    return hostname


def _transient_cooldown(failures: int) -> timedelta:
    minutes = min(2 ** max(failures - 1, 0), int(MAX_TRANSIENT_COOLDOWN.total_seconds() / 60))
    return timedelta(minutes=minutes)


def _retry_after(headers: dict[str, str], *, now: datetime | None = None) -> timedelta | None:
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "").strip()
    if re.fullmatch(r"\d+", value):
        return timedelta(seconds=max(1, min(int(value), 86400)))
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = parsed.astimezone(UTC) - (now or datetime.now(UTC))
    return min(max(delta, timedelta(seconds=1)), timedelta(days=1))


async def _read_limited(response: httpx.Response, maximum: int) -> bytes:
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > maximum:
        raise SourceRequestFailed(
            f"数据源响应超过 {maximum} 字节限制", error_kind="response_too_large"
        )
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > maximum:
            raise SourceRequestFailed(
                f"数据源响应超过 {maximum} 字节限制", error_kind="response_too_large"
            )
        chunks.append(chunk)
    return b"".join(chunks)
