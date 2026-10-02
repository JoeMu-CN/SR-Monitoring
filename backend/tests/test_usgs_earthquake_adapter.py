"""USGS 全天地震速报内置适配器协议级测试（MockTransport，不访问真实网络）。

固定指纹向量直接取自运行库 raw_signals（source_id=49）中声明式适配器真实落库的
fingerprint，用于证明内置适配器与同 spec 的 DeclarativeSourceAdapter 指纹逐字节一致。
"""

import asyncio
import json

import httpx
import pytest

from app.signals.declarative import AdapterSpec, DeclarativeSourceAdapter
from app.signals.router import build_pull_adapter
from app.signals.sources import RawSourceItem, SourceFetchError, UsgsEarthquakeAdapter

USGS_SOURCE_CODE = "usgs-earthquake-day"
USGS_ENDPOINT = (
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
)

# 与主库 data_sources.code='usgs-earthquake-day'（id=49）当前 adapter_config 完全一致。
USGS_SPEC_PAYLOAD: dict[str, object] = {
    "format": "json",
    "mapping": {
        "external_id": "id",
        "title": "properties.title",
        "content": "properties.place",
        "url": "properties.url",
        "published_at": None,
    },
    "request": {
        "url": USGS_ENDPOINT,
        "method": "GET",
        "params": {},
        "headers": {},
        "timeout_seconds": 15.0,
        "max_response_bytes": 10 * 1024 * 1024,
    },
    "max_items": 1000,
    "items_path": "features",
    "items_selector": None,
    "fingerprint_fields": ["external_id", "title"],
}

FIXTURE_FEATURES: list[dict[str, object]] = [
    {
        "id": "us7000thzz",
        "properties": {
            "title": "M 5.0 - South Atlantic Ocean",
            "place": "South Atlantic Ocean",
            "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000thzz",
        },
    },
    {
        "id": "hv75037752",
        "properties": {
            "title": "M 2.4 - 9 km SSE of Pāhala, Hawaii",
            "place": "9 km SSE of Pāhala, Hawaii",
            "url": "https://earthquake.usgs.gov/earthquakes/eventpage/hv75037752",
        },
    },
]

# 运行库 raw_signals（source_id=49）中的真实落库指纹，可作为重放固定向量。
EXPECTED_FINGERPRINTS: dict[str, str] = {
    "us7000thzz": "9b687e1dcbe1aec06bc428f35c766e222ba5ec999803f04d0dd42b8acb61276b",
    "hv75037752": "1a6aa121d71cd6e07d1a3f4cb67edcb086a8ba2e75dcc2d74108d27032e9432f",
}


def _geojson(features: list[dict[str, object]]) -> str:
    return json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False
    )


def _transport(payload: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == USGS_ENDPOINT
        return httpx.Response(200, text=payload)

    return httpx.MockTransport(handler)


def _declarative_adapter(transport: httpx.AsyncBaseTransport) -> DeclarativeSourceAdapter:
    spec = AdapterSpec.model_validate(USGS_SPEC_PAYLOAD)
    return DeclarativeSourceAdapter(USGS_SOURCE_CODE, spec, transport=transport)


def test_fetch_maps_feature_fields() -> None:
    adapter = UsgsEarthquakeAdapter(transport=_transport(_geojson(FIXTURE_FEATURES)))
    items = asyncio.run(adapter.fetch())

    assert len(items) == 2
    first = items[0]
    assert first.external_id == "us7000thzz"
    assert first.title == "M 5.0 - South Atlantic Ocean"
    # content 取 properties.place（声明式 spec 的映射）
    assert first.content == "South Atlantic Ocean"
    assert first.url == "https://earthquake.usgs.gov/earthquakes/eventpage/us7000thzz"
    # properties.time 为 epoch 毫秒，spec 不映射 published_at
    assert first.published_at is None


def test_normalize_and_fingerprint_match_declarative_spec() -> None:
    transport = _transport(_geojson(FIXTURE_FEATURES))
    usgs = UsgsEarthquakeAdapter(transport=transport)
    declarative = _declarative_adapter(transport)

    usgs_items = asyncio.run(usgs.fetch())
    declarative_items = asyncio.run(declarative.fetch())
    assert [item.external_id for item in usgs_items] == [
        item.external_id for item in declarative_items
    ]

    for usgs_item, declarative_item in zip(usgs_items, declarative_items, strict=True):
        usgs_signal = usgs.normalize(usgs_item)
        declarative_signal = declarative.normalize(declarative_item)
        # normalize 结果逐字段一致
        assert usgs_signal.model_dump() == declarative_signal.model_dump()
        # fingerprint 与声明式适配器逐字节一致，并命中运行库真实落库固定向量
        assert usgs.fingerprint(usgs_signal) == declarative.fingerprint(
            declarative_signal
        )
        assert (
            usgs.fingerprint(usgs_signal)
            == EXPECTED_FINGERPRINTS[usgs_signal.external_id or ""]
        )


def test_fingerprint_only_covers_external_id_and_title() -> None:
    adapter = UsgsEarthquakeAdapter()
    base = adapter.normalize(
        RawSourceItem(
            external_id="us7000thzz",
            title="M 5.0 - South Atlantic Ocean",
            content="South Atlantic Ocean",
            url="https://earthquake.usgs.gov/earthquakes/eventpage/us7000thzz",
        )
    )
    # content/url 不同但 external_id + title 相同：指纹必须一致（只用指纹字段子集）
    changed = adapter.normalize(
        RawSourceItem(
            external_id="us7000thzz",
            title="M 5.0 - South Atlantic Ocean",
            content="另一个地点",
            url="https://example.com/other",
        )
    )
    assert adapter.fingerprint(base) == adapter.fingerprint(changed)
    assert adapter.fingerprint(base) == EXPECTED_FINGERPRINTS["us7000thzz"]


def test_healthcheck_fails_on_empty_features() -> None:
    adapter = UsgsEarthquakeAdapter(transport=_transport(_geojson([])))
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False


def test_fetch_raises_on_non_json() -> None:
    adapter = UsgsEarthquakeAdapter(transport=_transport("<html>not json</html>"))
    with pytest.raises(SourceFetchError):
        asyncio.run(adapter.fetch())
    # healthcheck 沿用内置适配器范式：捕获 SourceFetchError 并报告失败
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False


def test_build_pull_adapter_registers_usgs() -> None:
    adapter = build_pull_adapter(USGS_SOURCE_CODE)
    assert isinstance(adapter, UsgsEarthquakeAdapter)
    assert adapter.source_code == USGS_SOURCE_CODE


def test_transport_handler_is_called_once_per_fetch() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200, content=_geojson(FIXTURE_FEATURES).encode("utf-8")
        )

    adapter = UsgsEarthquakeAdapter(transport=httpx.MockTransport(handler))
    asyncio.run(adapter.fetch())
    assert calls == [USGS_ENDPOINT]
