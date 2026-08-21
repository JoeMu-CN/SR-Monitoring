"""UflpaEntityAdapter（美国 UFLPA 实体清单 HTML 表格）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import UflpaEntityAdapter

# 模拟 DHS 实体清单页的表格结构（含表头 + 实体行 + 无关导航）
_HTML = (
    "<html><body>\n"
    "<p>An official website of the United States government</p>\n"
    "<h1>UFLPA Entity List</h1>\n"
    "<p>Entities identified as party to the commission of forced labor\n"
    "in the Xinjiang region of the People's Republic of China.</p>\n"
    "<table><thead><tr><th>Name of Entity</th><th>Effective Date</th></tr></thead>\n"
    "<tbody>\n"
    "<tr><td>Baoding LYSZD Trade and Business Co., Ltd.</td><td>June 21, 2022</td></tr>\n"
    "<tr><td>Hetian Haolin Hair Accessories Co. Ltd. (and two aliases: Hotan Haolin Hair Accessories; and Hollin Hair Accessories)</td><td>June 21, 2022</td></tr>\n"  # noqa: E501
    "<tr><td>Xinjiang East Hope&amp;Nonferrous Metals Co., Ltd.</td><td>August 15, 2023</td></tr>\n"
    "</tbody></table>\n"
    "</body></html>\n"
)


def _adapter(handler) -> UflpaEntityAdapter:
    return UflpaEntityAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_entity_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    items = asyncio.run(_adapter(handler).fetch())
    assert len(items) == 3
    first = items[0]
    assert first.title == "Baoding LYSZD Trade and Business Co., Ltd."
    assert "June 21, 2022" in first.content
    assert first.published_at is not None
    assert first.published_at.year == 2022
    assert first.url == UflpaEntityAdapter.endpoint
    assert first.external_id.startswith("uflpa-")
    # HTML 实体已解码（&amp; -> &）
    assert "&amp;" not in first.title
    xinjiang = [it for it in items if "Xinjiang East Hope" in it.title]
    assert len(xinjiang) == 1
    assert "&amp;" not in xinjiang[0].title
    assert "&" in xinjiang[0].title
    # 含别名实体完整保留
    aliased = [it for it in items if "Hetian Haolin" in it.title]
    assert len(aliased) == 1
    assert "Hotan Haolin" in aliased[0].title


def test_fetch_external_id_stable_for_dedupe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    items1 = asyncio.run(_adapter(handler).fetch())
    items2 = asyncio.run(_adapter(handler).fetch())
    ids1 = {it.external_id for it in items1}
    ids2 = {it.external_id for it in items2}
    assert ids1 == ids2  # 指纹稳定：重复采集可去重
    assert len(ids1) == 3


def test_healthcheck_ok_and_failure() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    health = asyncio.run(_adapter(ok_handler).healthcheck())
    assert health.ok is True
    assert "3" in health.message

    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>no table</body></html>")

    health2 = asyncio.run(_adapter(empty_handler).healthcheck())
    assert health2.ok is False

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health3 = asyncio.run(_adapter(error_handler).healthcheck())
    assert health3.ok is False
