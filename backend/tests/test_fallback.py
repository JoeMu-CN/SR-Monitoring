"""监控轨 Crawl4AI 回退的单元测试。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import MonitorCrawl4AISettings
from app.signals.fallback import read_public_page_with_crawl4ai_for_monitor
from app.signals.request_control import SourceRequestFailed


def _settings(token: str = "token-x") -> MonitorCrawl4AISettings:
    return MonitorCrawl4AISettings(
        enabled=True,
        base_url="http://crawl4ai.test:11235",
        api_token=token,
        timeout_seconds=5,
    )


def _transport(handler):
    return httpx.MockTransport(handler)


def _run(url, *, settings, handler=None):
    """用 MockTransport 调用 fallback；handler 是 ``lambda req: Response``。"""
    transport = _transport(handler) if handler else _transport(
        lambda _r: httpx.Response(200, content=b'{"results":[{"markdown":"x"}]}')
    )
    return asyncio.run(
        read_public_page_with_crawl4ai_for_monitor(
            url, settings=settings, transport=transport
        )
    )


def test_fallback_returns_markdown_on_success() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200, content=b'{"results":[{"markdown":"# heading\\n\\nbody text"}]}'
        )

    settings = _settings()
    result = _run("https://official.example/page", settings=settings, handler=handler)

    assert "heading" in result
    assert captured["request"].headers["authorization"] == "Bearer token-x"


def test_fallback_raises_when_disabled() -> None:
    settings = MonitorCrawl4AISettings(
        enabled=False, base_url="http://x", api_token="tok", timeout_seconds=5
    )
    with pytest.raises(SourceRequestFailed, match="未配置"):
        asyncio.run(
            read_public_page_with_crawl4ai_for_monitor(
                "https://official.example/", settings=settings
            )
        )


def test_fallback_raises_when_token_missing() -> None:
    settings = MonitorCrawl4AISettings(
        enabled=True, base_url="http://x", api_token="", timeout_seconds=5
    )
    with pytest.raises(SourceRequestFailed, match="未配置"):
        asyncio.run(
            read_public_page_with_crawl4ai_for_monitor(
                "https://official.example/", settings=settings
            )
        )


def test_fallback_raises_on_non_2xx() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"detail":"Authentication required"}')

    with pytest.raises(SourceRequestFailed, match="401"):
        _run("https://official.example/", settings=_settings(), handler=handler)


def test_fallback_raises_on_empty_markdown() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"results":[{"markdown":""}]}')

    with pytest.raises(SourceRequestFailed, match="空 Markdown"):
        _run("https://official.example/", settings=_settings(), handler=handler)


def test_fallback_parses_new_markdown_dict_shape() -> None:
    """Crawl4AI 新版返回 markdown 为 dict（raw_markdown 键），需兼容解析。"""
    body = (
        b'{"results":[{"markdown":{'
        b'"raw_markdown":"# raw heading\\n\\nraw body",'
        b'"fit_markdown":"# fit",'
        b'"markdown_with_citations":""}}]}'
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    result = _run("https://official.example/page", settings=_settings(), handler=handler)
    assert "raw heading" in result
    assert "raw body" in result


def test_fallback_dict_markdown_missing_raw_falls_back_to_fit() -> None:
    """新版 dict 若缺 raw_markdown 返回空并报错（不产生半成品）。"""
    body = b'{"results":[{"markdown":{"fit_markdown":"# fit only"}}]}'

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with pytest.raises(SourceRequestFailed, match="空 Markdown"):
        _run("https://official.example/", settings=_settings(), handler=handler)


def test_fallback_rejects_http_scheme() -> None:
    """回退路径同样要求 https，避免 Crawl4AI 出私网。"""
    settings = _settings()
    with pytest.raises(SourceRequestFailed, match="仅允许访问 HTTPS"):
        asyncio.run(
            read_public_page_with_crawl4ai_for_monitor(
                "http://example.com/page", settings=settings
            )
        )
