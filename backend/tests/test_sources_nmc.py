"""中央气象台信息源适配器协议级测试（MockTransport，不访问真实网络）。"""

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
                "title": "浙江省水利厅、浙江省气象台发布山洪灾害橙色预警",
                "url": "/publish/alarm/33000033200000_20260807181838.html",
                "pic": "https://image.nmc.cn/assets/img/alarm/p0024004.png",
            },
            {
                "alertid": "14000041600000_20260807165704",
                "issuetime": "2026/08/07 16:57",
                "title": "中央气象台发布台风蓝色预警",
                "url": "/publish/alarm/14000041600000_20260807165704.html",
                "pic": "https://image.nmc.cn/assets/img/alarm/p0021003.png",
            },
            {
                "alertid": "37000041600000_20260807160000",
                "issuetime": "2026/08/07 16:00",
                "title": "山东省气象台发布大风蓝色预警",
                "url": "/publish/alarm/37000041600000_20260807160000.html",
                "pic": "",
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
    # 第 3 条是普通蓝色预警，采集端已过滤；台风蓝色与橙色保留。
    assert len(items) == 2
    first = items[0]
    assert first.external_id == "33000033200000_20260807181838"
    assert "山洪灾害" in first.title
    assert first.url == "https://www.nmc.cn/publish/alarm/33000033200000_20260807181838.html"
    assert first.published_at is not None
    assert first.published_at.utcoffset() is not None
    assert "台风" in items[1].title


def test_fetch_drops_low_level_alerts_except_typhoon() -> None:
    rows = [
        {
            "alertid": "a-blue",
            "issuetime": "2026/08/07 12:00",
            "title": "河北省石家庄市气象台发布暴雨蓝色预警",
            "url": "/publish/alarm/a-blue.html",
        },
        {
            "alertid": "a-yellow",
            "issuetime": "2026/08/07 12:10",
            "title": "湖北省武汉市气象台发布高温黄色预警",
            "url": "/publish/alarm/a-yellow.html",
        },
        {
            "alertid": "a-typhoon-blue",
            "issuetime": "2026/08/07 12:20",
            "title": "中央气象台发布台风蓝色预警",
            "url": "/publish/alarm/a-typhoon-blue.html",
        },
        {
            "alertid": "a-orange",
            "issuetime": "2026/08/07 12:30",
            "title": "广东省深圳市气象台发布暴雨橙色预警",
            "url": "/publish/alarm/a-orange.html",
        },
        {
            "alertid": "a-no-level",
            "issuetime": "2026/08/07 12:40",
            "title": "某地气象台发布气象风险提示",
            "url": "/publish/alarm/a-no-level.html",
        },
    ]
    adapter = _adapter(
        lambda request: httpx.Response(200, text=_alarm_payload(rows=rows))
    )
    items = asyncio.run(adapter.fetch())

    # 保留：台风蓝色（豁免）、橙色、无级别词（fail-open）；丢弃：普通蓝/黄。
    assert [item.external_id for item in items] == [
        "a-typhoon-blue",
        "a-orange",
        "a-no-level",
    ]
    assert adapter._skipped_low_level == 2


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
    assert health.message == "返回 2 条预警（已过滤低级别 1 条）"


def test_healthcheck_ok_when_all_alerts_filtered() -> None:
    rows = [
        {
            "alertid": "only-blue",
            "issuetime": "2026/08/07 12:00",
            "title": "河北省气象台发布大风蓝色预警",
            "url": "/publish/alarm/only-blue.html",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_alarm_payload(rows=rows))

    health = asyncio.run(_adapter(handler).healthcheck())
    assert health.ok is True
    assert health.message == "返回 0 条预警（已过滤低级别 1 条）"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False
