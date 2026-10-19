"""FxRatesAdapter / SseShippingAdapter（汇率 + 航运指数）单测。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

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

_SHIPPING_MD_NO_DATE = """
| 航线 | 上期 | 本期 | 与上期比涨跌 (%) |
| --- | --- | --- | --- |
| 中国出口集装箱运价综合指数 | 1839.61 | 1846.96 | 0.4 |
| 日本航线 (JAPAN SERVICE) | 941.44 | 896.71 | -4.8 |
"""

# Crawl4AI 渲染后的真实形态：表头按单元格折行，「本期」在上一行行尾、日期在下一行行首。
_SHIPPING_MD_WRAPPED_HEADER = """
中国出口集装箱运价指数 CHINA CONTAINERIZED FREIGHT INDEX
| 航线  | 上期
2026-09-30  | 本期
2026-10-09  | 与上期比涨跌
(%)  |
| --- | --- | --- | --- |
| 中国出口集装箱运价综合指数 | 1923.93  | 1912.38  | -0.6  |
| 日本航线 (JAPAN SERVICE) | 1050.11  | 1043.62  | -0.6  |
| 欧洲航线 (EUROPE SERVICE) | 2416.45  | 2392.90  | -1.0  |
"""

_SHIPPING_BEIJING = timezone(timedelta(hours=8))


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

    def test_fetch_reads_current_period_date_from_header(self) -> None:
        # Given
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD)

        # When
        items = asyncio.run(self._adapter(handler).fetch())

        # Then：表头「本期 2026-08-14」按北京时间零点落到每一条
        assert len(items) == 4
        for item in items:
            assert item.published_at == datetime(2026, 8, 14, tzinfo=_SHIPPING_BEIJING)

    def test_normalize_converts_beijing_period_date_to_utc(self) -> None:
        # Given
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD)

        # When
        adapter = self._adapter(handler)
        signal = adapter.normalize(asyncio.run(adapter.fetch())[0])

        # Then：北京 2026-08-14 00:00 转 UTC 后为前一日 16:00，不发生 8 小时错位
        assert signal.published_at == datetime(2026, 8, 13, 16, tzinfo=UTC)

    def test_fetch_when_header_has_no_date_leaves_published_at_empty(self) -> None:
        # Given
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD_NO_DATE)

        # When
        adapter = self._adapter(handler)
        items = asyncio.run(adapter.fetch())

        # Then：解析不到日期不抛错，沿用 None 兜底
        assert len(items) == 2
        assert all(item.published_at is None for item in items)
        assert adapter.normalize(items[0]).published_at is None

    def test_fetch_reads_date_from_crawl4ai_wrapped_header(self) -> None:
        # Given：真实渲染把表头折行，「本期」与日期不在同一行
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD_WRAPPED_HEADER)

        # When
        adapter = self._adapter(handler)
        items = asyncio.run(adapter.fetch())

        # Then：跨行命中「本期 2026-10-09」，每条都拿到北京时区零点
        assert len(items) == 3
        assert all(
            item.published_at == datetime(2026, 10, 9, tzinfo=_SHIPPING_BEIJING)
            for item in items
        )

    def test_normalize_converts_wrapped_header_date_to_utc(self) -> None:
        # Given
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_SHIPPING_MD_WRAPPED_HEADER)

        # When
        adapter = self._adapter(handler)
        signal = adapter.normalize(asyncio.run(adapter.fetch())[0])

        # Then：北京 2026-10-09 00:00 转 UTC 后为前一日 16:00，不发生 8 小时错位
        assert signal.published_at == datetime(2026, 10, 8, 16, tzinfo=UTC)

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
