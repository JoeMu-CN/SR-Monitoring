"""CustomsAnnouncementAdapter（海关总署公告，Crawl4AI 渲染）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import CustomsAnnouncementAdapter

_MARKDOWN = """
* [中华人民共和国海关总署](http://www.customs.gov.cn/)

[海关总署公告2026年第121号（关于修改海关总署公告2019年第170号、2020年第73号部分条款的公告）](http://www.customs.gov.cn/customs/2026-08/19/article_2026081918055713032.html)
2026-08-19

[海关总署公告2026年第117号（关于废止海关总署公告2001年第3号的公告）](http://www.customs.gov.cn/customs/2026-08/12/article_2026081218115130744.html)
2026-08-12

[商务部新闻发言人就出口管制答记者问](http://www.customs.gov.cn/customs/2026-08/10/article_xxx.html)
"""


def _adapter(handler) -> CustomsAnnouncementAdapter:
    return CustomsAnnouncementAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_customs_announcements() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MARKDOWN)

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2  # 只收"海关总署公告"开头，排除商务部新闻
    first = items[0]
    assert first.title.startswith("海关总署公告2026年第121号")
    assert "customs.gov.cn" in first.url  # type: ignore[operator]
    assert first.published_at is not None
    assert first.external_id.startswith("customs-")


def test_fetch_deduplicates_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MARKDOWN)

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    urls = [it.url for it in items]
    assert len(urls) == len(set(urls))


def test_normalize_and_fingerprint_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MARKDOWN)

    adapter = _adapter(handler)
    item = asyncio.run(adapter.fetch())[0]
    signal = adapter.normalize(item)
    assert signal.title == item.title
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)


def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MARKDOWN)

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is True
    assert health.message == "返回 2 条公告"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False


def test_fetch_empty_markdown_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no content</html>")

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert items == []
