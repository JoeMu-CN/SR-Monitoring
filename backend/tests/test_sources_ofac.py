"""OFAC SDN 数据源适配器协议测试。"""

import asyncio
import csv
import io
from collections.abc import Callable

import httpx
import pytest

from app.signals.sources import OfacSdnAdapter, RawSourceItem, SourceFetchError


def _sdn_csv(rows: list[list[str]] | None = None) -> str:
    rows = rows or [
        [
            "36", "AEROCARIBBEAN AIRLINES", "-0-", "CUBA", "-0-", "-0-",
            "-0-", "-0-", "-0-", "-0-", "-0-", "",
        ],
        [
            "306", "BANCO NACIONAL DE CUBA", "-0-", "CUBA", "-0-", "-0-",
            "-0-", "-0-", "-0-", "-0-", "-0-", "a.k.a. 'BNC'.",
        ],
    ]
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue()


def _adapter(handler: Callable[[httpx.Request], httpx.Response]) -> OfacSdnAdapter:
    return OfacSdnAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_rows_without_header() -> None:
    adapter = _adapter(lambda _request: httpx.Response(200, text=_sdn_csv()))
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    assert items[0].external_id == "ofac-sdn-36"
    assert items[0].title.endswith("AEROCARIBBEAN AIRLINES")
    assert "国家/地区：CUBA" in items[0].content
    assert "备注：a.k.a. 'BNC'." in items[1].content


def test_fetch_raises_on_http_error() -> None:
    adapter = _adapter(lambda _request: httpx.Response(503, text="unavailable"))
    with pytest.raises(SourceFetchError):
        asyncio.run(adapter.fetch())


def test_fetch_follows_ofac_redirect() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://cdn.example/sdn.csv"})
        return httpx.Response(200, text=_sdn_csv())

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    assert requests == [
        OfacSdnAdapter._endpoint,
        "https://cdn.example/sdn.csv",
    ]


def test_fetch_raises_on_short_row() -> None:
    adapter = _adapter(lambda _request: httpx.Response(200, text="1,only-two"))
    with pytest.raises(SourceFetchError):
        asyncio.run(adapter.fetch())


def test_fetch_ignores_ofac_dos_eof_marker() -> None:
    adapter = _adapter(lambda _request: httpx.Response(200, text=_sdn_csv() + "\x1a"))
    assert len(asyncio.run(adapter.fetch())) == 2


def test_normalize_and_fingerprint_are_stable() -> None:
    adapter = OfacSdnAdapter()
    item = RawSourceItem(
        external_id="ofac-sdn-36",
        title="OFAC SDN 制裁名单：AEROCARIBBEAN AIRLINES",
        content="名称：AEROCARIBBEAN AIRLINES",
        url="https://sanctionslistservice.ofac.treas.gov/",
    )
    signal = adapter.normalize(item)
    assert signal.external_id == item.external_id
    assert str(signal.url) == item.url
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)


def test_healthcheck_ok() -> None:
    adapter = _adapter(lambda _request: httpx.Response(200, text=_sdn_csv()))
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is True
    assert health.message == "返回 2 条制裁条目"
