"""EuOfficialJournalAdapter（欧盟官方公报 SPARQL）单测。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.signals.schemas import ManualSignalInput
from app.signals.sources import EuOfficialJournalAdapter, SourceFetchError

_SPARQL_JSON = {
    "head": {"vars": ["celex", "date", "title"]},
    "results": {
        "bindings": [
            {
                "celex": {"type": "literal", "value": "32026R1115R(01)"},
                "date": {"type": "literal",
                "datatype": "http://www.w3.org/2001/XMLSchema#date",
                "value": "2026-08-19"},
                "title": {
                    "type": "literal",
                    "xml:lang": "en",
                    "value": "Corrigendum to Commission Implementing Regulation (EU) 2026/1115 of 26 May 2026 amending Regulation (EC) No 429/2008 as regards the application form for authorisations of feed additives",  # noqa: E501
                },
            },
            {
                "celex": {"type": "literal", "value": "32026D1949"},
                "date": {"type": "literal",
                "datatype": "http://www.w3.org/2001/XMLSchema#date",
                "value": "2026-08-13"},
                "title": {
                    "type": "literal",
                    "xml:lang": "en",
                    "value": "COMMISSION IMPLEMENTING DECISION (EU) 2026/1949 of 13 August 2026 amending the Annexes to Implementing Decision (EU) 2025/1582",  # noqa: E501
                },
            },
        ]
    },
}


def _adapter(handler) -> EuOfficialJournalAdapter:
    return EuOfficialJournalAdapter(
        transport=httpx.MockTransport(handler),
        lookback_days=7,
    )


def _sparql_handler(request: httpx.Request) -> httpx.Response:
    assert "sparql" in str(request.url)
    assert "format=json" in str(request.url)
    return httpx.Response(200, content=json.dumps(_SPARQL_JSON).encode("utf-8"))


def test_fetch_parses_sparql_bindings() -> None:
    adapter = _adapter(_sparql_handler)
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    first = items[0]
    assert first.external_id == "euoj-32026R1115R(01)"
    assert "Corrigendum" in first.title
    assert "CELEX：32026R1115R(01)" in first.content
    assert "eur-lex.europa.eu" in first.url  # type: ignore[operator]
    assert first.extra.get("celex") == "32026R1115R(01)"


def test_fetch_deduplicates_celex() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(_SPARQL_JSON).encode("utf-8"))

    adapter = _adapter(handler)
    items = asyncio.run(adapter.fetch())
    seen = {item.external_id for item in items}
    assert len(seen) == len(items)


def test_normalize_and_fingerprint_roundtrip() -> None:
    adapter = _adapter(_sparql_handler)
    item = asyncio.run(adapter.fetch())[0]
    signal = adapter.normalize(item)
    assert isinstance(signal, ManualSignalInput)
    assert signal.title == item.title
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)


def test_healthcheck_ok() -> None:
    adapter = _adapter(_sparql_handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is True
    assert health.message == "返回 2 条法规"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    adapter = _adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False


def test_fetch_rejects_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    adapter = _adapter(handler)
    with pytest.raises(SourceFetchError):
        asyncio.run(adapter.fetch())
