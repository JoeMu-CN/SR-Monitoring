"""BisEntityListAdapter（美国商务部 BIS 实体清单 HTML 表格）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import BisEntityListAdapter

# 模拟 BIS 实体清单表格（表头 + 实体行，含别名/地址/许可要求）
_HTML = (
    "<html><body>\n"
    "<table>\n"
    "<tr class='header'><th>Country</th><th>Entity</th><th>License requirement</th>"
    "<th>License review policy</th><th>Federal Register citation</th></tr>\n"
    "<tr><td>China</td><td>Huawei Technologies Co., Ltd.<br/>No. 2 Xinxi Road, Shenzhen.</td>"
    "<td>For all items subject to the EAR</td><td>Presumption of denial</td>"
    "<td>83 FR 44824</td></tr>\n"
    "<tr><td> </td><td>Semiconductor Manufacturing International (Beijing) Corporation, "
    "a.k.a., the following one alias:\n<br/>\u2014SMIC Beijing.\n<br/>\n<br/> "
    "No. 18 Wen Chang Road, Beijing Economic-Technological Development Area, "
    "Beijing 100176.</td><td>For all items subject to the EAR (See §§ 734.4(a)(9))</td>"
    "<td>Presumption of denial</td><td>86 FR 29193</td></tr>\n"
    "<tr><td>Russia</td><td>Limited Liability Company Yandex</td>"
    "<td>For all items subject to the EAR</td><td>Case-by-case</td><td>87 FR 10215</td></tr>\n"
    "</table>\n"
    "</body></html>\n"
)


def _adapter(handler) -> BisEntityListAdapter:
    return BisEntityListAdapter(transport=httpx.MockTransport(handler))


def test_fetch_parses_entity_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    items = asyncio.run(_adapter(handler).fetch())
    assert len(items) == 3
    huawei = [it for it in items if "Huawei" in it.title][0]
    # br 分隔后 title 只含名称（不含地址）
    assert huawei.title == "Huawei Technologies Co., Ltd."
    assert "China" in huawei.content
    assert "Presumption of denial" in huawei.content
    assert huawei.external_id.startswith("bis-")
    assert huawei.url == BisEntityListAdapter.endpoint
    # 别名实体完整保留（SMIC 含 a.k.a.，地址在 content）
    smic = [it for it in items if "Semiconductor" in it.title]
    assert len(smic) == 1
    assert "SMIC Beijing" in smic[0].content
    assert "Beijing Economic-Technological" in smic[0].content


def test_fetch_external_id_stable_for_dedupe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    ids1 = {it.external_id for it in asyncio.run(_adapter(handler).fetch())}
    ids2 = {it.external_id for it in asyncio.run(_adapter(handler).fetch())}
    assert ids1 == ids2
    assert len(ids1) == 3


def test_healthcheck_ok_and_failure() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    health = asyncio.run(_adapter(ok_handler).healthcheck())
    assert health.ok is True
    assert "3" in health.message

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health2 = asyncio.run(_adapter(error_handler).healthcheck())
    assert health2.ok is False
