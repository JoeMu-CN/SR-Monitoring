"""FxRatesAdapter / SseShippingAdapter（汇率 + 航运指数）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import FxRatesAdapter, SseShippingAdapter

_FX_JSON = (
    '{"result": "success", "time_last_update_utc": "Thu, 20 Aug 2026 00:02:31 +0000", '
    '"base_code": "USD", "rates": {"USD": 1, "CNY": 6.746073, "EUR": 0.858444, '
    '"JPY": 158.548543, "GBP": 0.735868}}'
)

_SHIPPING_MD = """
[首页](https://www.sse.net.cn/)

中国出口集装箱运价指数 CHINA CONTAINERIZED FREIGHT INDEX
| 航线 | 上期 2026-08-07 | 本期 2026-08-14 | 与上期比涨跌 (%) |
| --- | --- | --- | --- |
| 中国出口集装箱运价综合指数 | 1839.61 | 1846.96 | 0.4 |
| 日本航线 (JAPAN SERVICE) | 941.44 | 896.71 | -4.8 |
| 欧洲航线 (EUROPE SERVICE) | 2416.45 | 2392.90 | -1.0 |
| 美西航线 (W/C AMERICA SERVICE) | 1494.58 | 1544.28 | 3.3 |
"""


class TestFxRatesAdapter:
    def _adapter(self, handler) -> FxRatesAdapter:
        return FxRatesAdapter(transport=httpx.MockTransport(handler))

    def test_fetch_parses_key_currencies(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_FX_JSON.encode("utf-8"))

        items = asyncio.run(self._adapter(handler).fetch())
        assert len(items) == 4  # CNY/EUR/JPY/GBP
        cny = [it for it in items if it.extra.get("currency") == "CNY"][0]
        assert "6.7461" in cny.title
        assert "2026-08-20" in cny.content or "Thu, 20 Aug" in cny.content

    def test_healthcheck_ok(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_FX_JSON.encode("utf-8"))

        health = asyncio.run(self._adapter(handler).healthcheck())
        assert health.ok is True
        assert health.message == "返回 4 条汇率"

    def test_healthcheck_failed_on_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"result": "error"}')

        health = asyncio.run(self._adapter(handler).healthcheck())
        assert health.ok is False

    def test_normalize_roundtrip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_FX_JSON.encode("utf-8"))

        adapter = self._adapter(handler)
        item = asyncio.run(adapter.fetch())[0]
        signal = adapter.normalize(item)
        assert signal.title == item.title
        assert adapter.fingerprint(signal) == adapter.fingerprint(signal)


class TestSseShippingAdapter:
    def _adapter(self, handler) -> SseShippingAdapter:
        return SseShippingAdapter(transport=httpx.MockTransport(handler))

    def test_fetch_parses_index_table(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD)

        items = asyncio.run(self._adapter(handler).fetch())
        assert len(items) == 4
        comp = [it for it in items if "综合指数" in it.title][0]
        assert "1839.61" in comp.content
        assert "1846.96" in comp.content
        assert comp.extra.get("route") == "中国出口集装箱运价综合指数"

    def test_healthcheck_ok(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD)

        health = asyncio.run(self._adapter(handler).healthcheck())
        assert health.ok is True

    def test_healthcheck_failed_on_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>no table</html>")

        health = asyncio.run(self._adapter(handler).healthcheck())
        assert health.ok is False

    def test_normalize_roundtrip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD)

        adapter = self._adapter(handler)
        item = asyncio.run(adapter.fetch())[0]
        signal = adapter.normalize(item)
        assert signal.title == item.title
        assert adapter.fingerprint(signal) == adapter.fingerprint(signal)
