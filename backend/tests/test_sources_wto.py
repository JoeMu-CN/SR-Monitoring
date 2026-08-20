"""WtoNewsAdapter（WTO 新闻，Crawl4AI 渲染）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import WtoNewsAdapter

_MARKDOWN = """
* [home](https://www.wto.org/index.htm)
* [News](https://www.wto.org/english/news_e/news_e.htm)

WTO News and Events
===================

# Deadline to register for 2026 Public Forum is 21 August

18 August 2026

The deadline for online registration for this year's in-person Public Forum is 21 August.

###  United Kingdom launches safeguard investigation on polyethylene terephthalate

5 August 2026

On 5 August 2026, the United Kingdom notified the WTO's Committee on Safeguards
that it had initiated, on the same day, a safeguard investigation on imports of
certain polyethylene terephthalate (PET).

  * [News item](https://www.wto.org/english/news_e/news26_e/safe_gbr_05aug26_472_e.htm)

* * *

###  Russia requests dispute panel on EU carbon border adjustment

3 August 2026

Russia has requested the establishment of a dispute panel regarding the EU's
Carbon Border Adjustment Mechanism.

  * [News item](https://www.wto.org/english/news_e/news26_e/ds626_russ_e.htm)
"""


def _adapter(handler) -> WtoNewsAdapter:
    return WtoNewsAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_wto_news_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MARKDOWN)

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    first = items[0]
    assert "safeguard investigation" in first.title
    assert "PET" in first.content or "polyethylene" in first.content
    assert "safe_gbr_05aug26" in first.url  # type: ignore[operator]
    assert first.published_at is not None
    assert first.external_id.startswith("wto-")


def test_fetch_skips_navigation_headings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_MARKDOWN)

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    titles = [it.title for it in items]
    assert all("News" not in t or "safeguard" in t for t in titles)


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
    assert health.message == "返回 2 条新闻"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False


def test_fetch_rejects_empty_markdown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no content</html>")

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert items == []
