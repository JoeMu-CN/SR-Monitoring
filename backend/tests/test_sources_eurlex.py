"""EuOfficialJournalAdapter（欧盟官方公报 SPARQL）单测。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.signals.schemas import ManualSignalInput
from app.signals.sources import (
    EuComplianceAdapter,
    EuOfficialJournalAdapter,
    SourceFetchError,
)

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


# --- EuComplianceAdapter（P3 供应链合规关键词过滤） ---

_COMPLIANCE_JSON = {
    "head": {"vars": ["celex", "date", "title"]},
    "results": {
        "bindings": [
            {
                "celex": {"type": "literal", "value": "32026R1200"},
                "date": {"type": "literal", "value": "2026-08-20"},
                "title": {
                    "type": "literal",
                    "xml:lang": "en",
                    "value": "Commission Implementing Regulation (EU) 2026/1200 "
                    "on the carbon border adjustment mechanism transitional rules",
                },
            },
            {
                "celex": {"type": "literal", "value": "32026L0456"},
                "date": {"type": "literal", "value": "2026-08-18"},
                "title": {
                    "type": "literal",
                    "xml:lang": "en",
                    "value": "Directive (EU) 2026/456 on corporate sustainability "
                    "due diligence obligations for supply chains",
                },
            },
        ]
    },
}


def _compliance_adapter(handler) -> EuComplianceAdapter:
    return EuComplianceAdapter(
        transport=httpx.MockTransport(handler),
        lookback_days=7,
    )


def test_compliance_filters_to_relevant_regulations() -> None:
    """P3 适配器在 SPARQL 层注入 CONTAINS 关键词过滤（仅返回合规相关法规）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        # 断言请求 URL 已带 SPARQL 层关键词过滤条件（URL 编码，需解码后匹配）
        from urllib.parse import unquote_plus

        decoded = unquote_plus(str(request.url))
        assert "CONTAINS(LCASE(STR(?title))" in decoded
        assert "carbon border" in decoded
        # mock 返回 SPARQL 已过滤后的结果（仅合规条目）
        return httpx.Response(200, content=json.dumps(_COMPLIANCE_JSON).encode("utf-8"))

    adapter = _compliance_adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert len(items) == 2
    titles = {item.title for item in items}
    assert any("carbon border" in t.lower() for t in titles)
    assert any("due diligence" in t.lower() for t in titles)
    assert not any("feed additives" in t.lower() for t in titles)
    assert all(item.external_id.startswith("euoj-") for item in items)


def test_compliance_request_injects_keyword_filter() -> None:
    """合规适配器请求必须带 CONTAINS 过滤；基础适配器请求不带。"""
    from urllib.parse import unquote_plus

    from app.signals.sources import EuOfficialJournalAdapter

    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, content=json.dumps(_COMPLIANCE_JSON).encode("utf-8"))

    asyncio.run(_compliance_adapter(handler).fetch())
    assert "CONTAINS(LCASE(STR(?title))" in unquote_plus(captured[0])

    captured.clear()
    base = EuOfficialJournalAdapter(
        transport=httpx.MockTransport(handler), lookback_days=7
    )
    asyncio.run(base.fetch())
    assert "CONTAINS(LCASE(STR(?title))" not in unquote_plus(captured[0])


def test_compliance_no_match_returns_empty() -> None:
    """SPARQL 层无命中（mock 返回空）时返回空列表（不误报）。"""

    empty = {"head": {"vars": ["celex", "date", "title"]}, "results": {"bindings": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(empty).encode("utf-8"))

    adapter = _compliance_adapter(handler)
    items = asyncio.run(adapter.fetch())
    assert items == []


def test_compliance_healthcheck_reports_empty_as_failure() -> None:
    """无合规法规命中时 healthcheck 返回 ok=False（提示监控异常而非静默）。"""

    empty = {"head": {"vars": ["celex", "date", "title"]}, "results": {"bindings": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(empty).encode("utf-8"))

    adapter = _compliance_adapter(handler)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is False
