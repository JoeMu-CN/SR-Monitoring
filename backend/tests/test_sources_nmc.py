"""中央气象台数据源适配器协议级测试（MockTransport，不访问真实网络）。"""

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.signals.sources import (
    NmcWeatherAdapter,
    RawSourceItem,
    SourceFetchError,
)


def _alarm_payload(*, code: int = 0, rows: list[dict[str, object]] | None = None) -> str:
    if rows is None:
        rows = [
            {
                "alertid": "33000033200000_20260807181838",
                "issuetime": "2026/08/07 18:18",
                "title": "浙江省水利厅、浙江省气象台发布山洪灾害蓝色预警",
                "url": "/publish/alarm/33000033200000_20260807181838.html",
                "pic": "https://image.nmc.cn/assets/img/alarm/p0024004.png",
            },
            {
                "alertid": "14000041600000_20260807165704",
                "issuetime": "2026/08/07 16:57",
                "title": "山西省自然资源厅和山西省气象台发布地质灾害黄色预警",
                "url": "/publish/alarm/14000041600000_20260807165704.html",
                "pic": "https://image.nmc.cn/assets/img/alarm/p0021003.png",
            },
        ]
    return json.dumps({"msg": "success", "code": code, "data": {"page": {"list": rows}}})


def _adapter(handler: Callable[[httpx.Request], httpx.Response]) -> NmcWeatherAdapter:
    transport = httpx.MockTransport(handler)
    return NmcWeatherAdapter(transport=transport)


def test_fetch_parses_alarm_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["pageSize"] == "100"
        return httpx.Response(200, text=_alarm_payload())

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    first = items[0]
    assert first.external_id == "33000033200000_20260807181838"
    assert "山洪灾害" in first.title
    assert first.url == "https://www.nmc.cn/publish/alarm/33000033200000_20260807181838.html"
    assert first.published_at is not None
    assert first.published_at.utcoffset() is not None


def test_fetch_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    adapter = _adapter(handler)
    with pytest.raises(SourceFetchError):
        asyncio.run(adapter.fetch())


def test_fetch_raises_on_business_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_alarm_payload(code=1))

    adapter = _adapter(handler)
    with pytest.raises(SourceFetchError):
        asyncio.run(adapter.fetch())


def test_normalize_produces_manual_signal() -> None:
    item = RawSourceItem(
        external_id="14000041600000_20260807165704",
        title="地质灾害黄色预警",
        content="地质灾害黄色预警",
        url="https://www.nmc.cn/publish/alarm/x.html",
    )
    adapter = NmcWeatherAdapter()
    signal = adapter.normalize(item)
    assert signal.external_id == item.external_id
    assert signal.title == "地质灾害黄色预警"
    assert str(signal.url) == item.url
    assert signal.published_at is None


def test_fingerprint_is_stable() -> None:
    adapter = NmcWeatherAdapter()
    signal = adapter.normalize(
        RawSourceItem(external_id="x-1", title="台风蓝色预警", content="台风蓝色预警")
    )
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)
    other = adapter.normalize(
        RawSourceItem(external_id="x-1", title="台风蓝色预警", content="台风蓝色预警")
    )
    assert adapter.fingerprint(signal) == adapter.fingerprint(other)


def test_healthcheck_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_alarm_payload())

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is True
    assert health.message == "返回 2 条预警"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False
