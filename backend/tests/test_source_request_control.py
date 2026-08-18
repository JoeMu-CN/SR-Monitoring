import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import Session

import app.signals.request_control as request_control
from app.signals.models import SourceHostAccess
from app.signals.request_control import (
    ACCESS_BLOCK_COOLDOWN,
    PinnedIPBackend,
    PinnedIPTransport,
    SourceRequestFailed,
    classify_response,
    controlled_get,
)


def test_classify_response_blocks_forbidden_and_waf_pages() -> None:
    forbidden = classify_response(403)
    waf_page = classify_response(200, body_excerpt="创宇盾提示您：您的IP最近有可疑的攻击行为")

    assert forbidden.error_kind == "access_blocked"
    assert forbidden.cooldown == ACCESS_BLOCK_COOLDOWN
    assert waf_page.error_kind == "access_blocked"


def test_classify_response_respects_retry_after() -> None:
    decision = classify_response(429, {"Retry-After": "120"})

    assert decision.error_kind == "rate_limited"
    assert decision.cooldown == timedelta(seconds=120)


def test_classify_response_caps_retry_after_http_date() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    decision = classify_response(
        429,
        {"Retry-After": "Tue, 11 Aug 2026 00:00:00 GMT"},
        now=now,
    )

    assert decision.cooldown == timedelta(days=1)


def test_controlled_get_with_mock_transport_skips_persistent_guard() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text="ok"))

    response = asyncio.run(
        controlled_get(
            "https://official.example/events",
            maximum_bytes=1024,
            transport=transport,
        )
    )

    assert response.content == b"ok"


def test_controlled_get_rejects_waf_page_even_with_200() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text="创宇盾提示您：request blocked")
    )

    with pytest.raises(SourceRequestFailed, match="安全防护") as exc_info:
        asyncio.run(
            controlled_get(
                "https://official.example/events",
                maximum_bytes=1024,
                transport=transport,
            )
        )

    assert exc_info.value.error_kind == "access_blocked"


def test_host_lease_blocks_concurrency_and_persists_cooldown(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def session_factory() -> Session:
        return Session(
            bind=db_session.connection(),
            join_transaction_mode="create_savepoint",
        )

    monkeypatch.setattr(request_control, "SessionLocal", session_factory)
    now = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    lease = request_control.acquire_host_lease("https://official.example/events", now=now)

    with pytest.raises(request_control.SourceAccessDeferred, match="已有请求"):
        request_control.acquire_host_lease("https://official.example/other", now=now)

    decision = request_control.classify_response(403)
    request_control.complete_host_lease(
        lease,
        status_code=403,
        decision=decision,
        now=now,
    )
    db_session.expire_all()
    state = db_session.get(SourceHostAccess, "official.example")

    assert state is not None
    assert state.last_http_status == 403
    assert state.last_error_kind == "access_blocked"
    assert state.cooldown_until == now + ACCESS_BLOCK_COOLDOWN


def test_pinned_transport_keeps_url_and_delegates() -> None:
    """PinnedIPTransport 应保持 URL/Host 头不变（SNI 正确），仅委托内层 transport。"""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, text="ok")

    inner = httpx.MockTransport(handler)
    transport = PinnedIPTransport(
        ["1.1.1.1", "8.8.8.8"],
        "official.example",
        inner=inner,
    )

    response = asyncio.run(
        controlled_get(
            "https://official.example/events",
            maximum_bytes=1024,
            transport=transport,
        )
    )

    assert response.content == b"ok"
    sent = captured["request"]
    # URL 保持原始域名（SNI 依赖它）
    assert sent.url.host == "official.example"
    assert sent.url.path == "/events"
    # Host 头保持原始域名
    assert sent.headers["host"] == "official.example"


def test_pinned_backend_connects_to_pinned_ip() -> None:
    """PinnedIPBackend 应在 connect_tcp 层使用 pin 住的公网 IP。"""
    sent_hosts: list[str] = []

    class _FakeBackend:
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            sent_hosts.append(host)
            return None

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise AssertionError("不应使用 Unix socket")

        async def sleep(self, seconds):
            pass

    # 同时包含 IPv4 + IPv6：构造函数应自动 IPv4 优先
    backend = PinnedIPBackend(["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
    backend._inner = _FakeBackend()  # type: ignore[attr-defined]

    # 多次调用都返回首个 IP（IPv4 优先）；首个成功时不会轮换
    asyncio.run(backend.connect_tcp("official.example", 443))
    asyncio.run(backend.connect_tcp("official.example", 443))

    assert sent_hosts == ["1.1.1.1", "1.1.1.1"]


def test_pinned_transport_rejects_host_swap() -> None:
    """若请求被改写到非预期主机，transport 应立即拒绝。"""
    inner = httpx.MockTransport(lambda _r: httpx.Response(200, text="ok"))
    transport = PinnedIPTransport(["1.1.1.1"], "expected.example", inner=inner)

    with pytest.raises(RuntimeError, match="主机不匹配"):
        asyncio.run(
            controlled_get(
                "https://attacker.example/path",
                maximum_bytes=1024,
                transport=transport,
            )
        )


def test_pinned_transport_requires_non_empty_ips() -> None:
    with pytest.raises(ValueError, match="pinned_ips 不能为空"):
        PinnedIPTransport([], "example.com")
    with pytest.raises(ValueError, match="至少需要一个非空 IP"):
        PinnedIPTransport(["", ""], "example.com")
    with pytest.raises(ValueError, match="pinned_ips 不能为空"):
        PinnedIPBackend([])


def test_pinned_backend_preserves_port() -> None:
    """非默认端口必须从原 URL 透传到 pin 目标。"""
    sent: list[tuple[str, int]] = []

    class _FakeBackend:
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            sent.append((host, port))
            return None

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise AssertionError("不应使用 Unix socket")

        async def sleep(self, seconds):
            pass

    backend = PinnedIPBackend(["1.1.1.1"])
    backend._inner = _FakeBackend()  # type: ignore[attr-defined]

    asyncio.run(backend.connect_tcp("official.example", 8443))

    assert sent == [("1.1.1.1", 8443)]


def test_pinned_backend_supports_ipv6() -> None:
    """IPv6 地址在 connect_tcp 层直接透传（anyio 原生支持），无需 URL 方括号改写。"""
    sent: list[str] = []

    class _FakeBackend:
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            sent.append(host)
            return None

        async def connect_unix_socket(self, path, timeout=None, socket_options=None):
            raise AssertionError("不应使用 Unix socket")

        async def sleep(self, seconds):
            pass

    backend = PinnedIPBackend(["2606:4700:4700::1111"])
    backend._inner = _FakeBackend()  # type: ignore[attr-defined]

    asyncio.run(backend.connect_tcp("dualstack.example", 443))

    assert sent == ["2606:4700:4700::1111"]


def test_pinned_backend_retries_next_ip_on_connection_failure() -> None:
    """单个 IP 不可达时自动尝试下一个；任一 IP 可达即返回。"""
    attempts: list[str] = []

    class _FakeBackend:
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            attempts.append(host)
            # IPv6 都不可达，IPv4 可达
            if ":" in host:
                raise OSError(101, "Network unreachable")
            return f"stream-for-{host}"

        async def connect_unix_socket(self, *args, **kwargs):
            raise AssertionError("不应使用 Unix socket")

        async def sleep(self, seconds):
            pass

    # IPv4 在前：构造函数已自动 IPv4 优先排序
    backend = PinnedIPBackend(
        ["2606:4700:4700::1111", "1.1.1.1", "2606:4700:4700::2222", "8.8.8.8"]
    )
    backend._inner = _FakeBackend()  # type: ignore[attr-defined]

    stream = asyncio.run(backend.connect_tcp("example.com", 443))

    # IPv4 优先：1.1.1.1 第一个被尝试并成功
    assert stream == "stream-for-1.1.1.1"
    assert attempts == ["1.1.1.1"]


def test_pinned_backend_falls_through_all_ips_then_raises() -> None:
    """全部 IP 都不可达时抛出包含所有 attempts 的 OSError。"""
    attempts: list[str] = []

    class _FakeBackend:
        async def connect_tcp(
            self, host, port, timeout=None, local_address=None, socket_options=None
        ):
            attempts.append(host)
            raise OSError(113, "No route to host")

        async def connect_unix_socket(self, *args, **kwargs):
            raise AssertionError("不应使用 Unix socket")

        async def sleep(self, seconds):
            pass

    backend = PinnedIPBackend(["1.1.1.1", "8.8.8.8"])
    backend._inner = _FakeBackend()  # type: ignore[attr-defined]

    with pytest.raises(OSError, match="全部 2 个 IP 不可达"):
        asyncio.run(backend.connect_tcp("example.com", 443))

    assert attempts == ["1.1.1.1", "8.8.8.8"]
