"""研究轨受控单页读取测试（只使用 httpx MockTransport）。"""

import asyncio

import httpx
import pytest

from app.research.web import read_public_page
from app.signals.request_control import SourceRequestFailed
from app.signals.sources import SourceFetchError


def _run(coro):
    return asyncio.run(coro)


def test_read_public_page_records_redirect_chain_and_visible_excerpt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/next"})
        if request.url.path == "/next":
            return httpx.Response(301, headers={"Location": "https://official.example/final"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=(
                "<html><head><script>secret()</script><style>.x{}</style></head>"
                "<body><h1>官方公告</h1><p>供应链信息正常</p>"
                "<noscript>不要显示</noscript></body></html>"
            ).encode(),
        )

    result = _run(
        read_public_page(
            "https://official.example/start",
            transport=httpx.MockTransport(handler),
        )
    )

    assert result.requested_url == "https://official.example/start"
    assert result.final_url == "https://official.example/final"
    assert result.redirect_chain == (
        "https://official.example/next",
        "https://official.example/final",
    )
    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.excerpt == "官方公告 供应链信息正常"
    assert result.reader == "direct_http"
    assert "secret" not in result.excerpt


@pytest.mark.parametrize("url", ["http://official.example/page", "https://127.0.0.1/page"])
def test_read_public_page_rejects_non_https_or_private_ip(url: str) -> None:
    with pytest.raises(SourceFetchError):
        _run(read_public_page(url, transport=httpx.MockTransport(lambda _: httpx.Response(200))))


def test_read_public_page_rejects_response_over_limit() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 1025))

    with pytest.raises(SourceRequestFailed) as exc_info:
        _run(
            read_public_page(
                "https://official.example/page",
                maximum_bytes=1024,
                transport=transport,
            )
        )

    assert exc_info.value.error_kind == "response_too_large"


@pytest.mark.parametrize(
    ("status_code", "error_kind"),
    [(403, "access_blocked"), (429, "rate_limited")],
)
def test_read_public_page_classifies_blocked_and_rate_limited(
    status_code: int, error_kind: str
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status_code))

    with pytest.raises(SourceRequestFailed) as exc_info:
        _run(read_public_page("https://official.example/page", transport=transport))

    assert exc_info.value.error_kind == error_kind
    assert exc_info.value.status_code == status_code


def test_read_public_page_rejects_redirect_without_location() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(302))

    with pytest.raises(SourceRequestFailed) as exc_info:
        _run(read_public_page("https://official.example/page", transport=transport))

    assert exc_info.value.error_kind == "redirect"
    assert exc_info.value.status_code == 302


def test_read_public_page_revalidates_redirect_target() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})
    )

    with pytest.raises(SourceFetchError):
        _run(read_public_page("https://official.example/page", transport=transport))


def test_read_public_page_rejects_redirect_limit() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"Location": f"https://official.example{request.url.path}x"},
        )
    )

    with pytest.raises(SourceRequestFailed) as exc_info:
        _run(
            read_public_page(
                "https://official.example/page",
                maximum_redirects=1,
                transport=transport,
            )
        )

    assert exc_info.value.error_kind == "redirect_limit"


def test_read_public_page_falls_back_to_crawl4ai_for_empty_static_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEARCH_CRAWL4AI_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_CRAWL4AI_BASE_URL", "http://crawl4ai:11235")
    monkeypatch.setenv("RESEARCH_CRAWL4AI_API_TOKEN", "test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "crawl4ai":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://official.example/dynamic",
                            "status_code": 200,
                            "markdown": "动态页面已渲染：官方公告正文。",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<html><body><div id='app'></div><script>render()</script></body></html>",
        )

    result = _run(
        read_public_page(
            "https://official.example/dynamic",
            transport=httpx.MockTransport(handler),
        )
    )

    assert result.content_type == "text/markdown"
    assert result.excerpt == "动态页面已渲染：官方公告正文。"
    assert result.reader == "crawl4ai"


def test_read_public_page_does_not_use_crawl4ai_to_bypass_waf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEARCH_CRAWL4AI_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_CRAWL4AI_BASE_URL", "http://crawl4ai:11235")
    monkeypatch.setenv("RESEARCH_CRAWL4AI_API_TOKEN", "test-token")
    crawler_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal crawler_calls
        if request.url.host == "crawl4ai":
            crawler_calls += 1
            return httpx.Response(200, json={"results": []})
        return httpx.Response(403, text="WAF blocked")

    with pytest.raises(SourceRequestFailed) as exc_info:
        _run(
            read_public_page(
                "https://official.example/blocked",
                transport=httpx.MockTransport(handler),
            )
        )

    assert exc_info.value.error_kind == "access_blocked"
    assert crawler_calls == 0
