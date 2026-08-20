"""FmprcPressAdapter（外交部例行记者会）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import FmprcPressAdapter

_HTML = """
<html>
<head><title>例行记者会</title></head>
<body>
<ul>
<li><a href="./202608/t20260820_12007399.shtml">
2026年8月20日外交部发言人林剑主持例行记者会（2026-08-20）</a></li>
<li><a href="./202608/t20260819_12006693.shtml">
2026年8月19日外交部发言人林剑主持例行记者会（2026-08-19）</a></li>
<li><a href="./202608/t20260818_12006053.shtml">
2026年8月18日外交部发言人林剑主持例行记者会（2026-08-18）</a></li>
</ul>
</body>
</html>
"""


def _adapter(handler) -> FmprcPressAdapter:
    return FmprcPressAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_press_conferences() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    items = asyncio.run(_adapter(handler).fetch())
    assert len(items) == 3
    first = items[0]
    assert "例行记者会" in first.title
    assert "2026-08-20" in first.title
    assert "mfa.gov.cn" in first.url  # type: ignore[operator]
    assert first.published_at is not None
    assert first.external_id.startswith("fmprc-")


def test_fetch_skips_non_conference_links() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 把第 2 条列表项标题改成"常驻联合国代表机构"（应被过滤）
        html = _HTML.replace(
            "2026年8月19日外交部发言人林剑主持例行记者会",
            "常驻联合国代表机构",
        )
        return httpx.Response(200, text=html)

    items = asyncio.run(_adapter(handler).fetch())
    assert len(items) == 2  # 1 条因"常驻机构"被过滤


def test_normalize_and_fingerprint_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    adapter = _adapter(handler)
    item = asyncio.run(adapter.fetch())[0]
    signal = adapter.normalize(item)
    assert signal.title == item.title
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)


def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    health = asyncio.run(_adapter(handler).healthcheck())
    assert health.ok is True
    assert health.message == "返回 3 条记者会"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False
