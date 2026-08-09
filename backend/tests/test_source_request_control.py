import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import Session

import app.signals.request_control as request_control
from app.signals.models import SourceHostAccess
from app.signals.request_control import (
    ACCESS_BLOCK_COOLDOWN,
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
