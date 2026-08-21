"""StatsPmiAdapter（统计局制造业 PMI，E1 宏观经济）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import StatsPmiAdapter

_LIST_HTML = (
    "<html><body>\n"
    "<ul>\n"
    "<li><a href=\"./202607/t20260731_1964253.html\">"
    "2026年7月中国采购经理指数运行情况</a></li>\n"
    "<li><a href=\"./202606/t20260630_1955000.html\">"
    "2026年6月中国采购经理指数运行情况</a></li>\n"
    "</ul>\n"
    "</body></html>\n"
)

_DETAIL_HTML = """<html><body>
国家统计局服务业调查中心 中国物流与采购联合会
一、中国制造业采购经理指数运行情况
7 月份，制造业采购经理指数（PMI）为 49.2% ，比上月下降 1.1 个百分点，景气水平有所回落。
从企业规模看，大、中、小型企业 PMI 分别为 49.5% 、 49.0% 和 47.7% 。
</body></html>
"""


def _adapter(handler) -> StatsPmiAdapter:
    return StatsPmiAdapter(transport=httpx.MockTransport(handler))


def _handler():
    def handler(request: httpx.Request) -> httpx.Response:
        if "t20260731" in str(request.url):
            return httpx.Response(200, text=_DETAIL_HTML)
        if "zxfb/" in str(request.url):
            return httpx.Response(200, text=_LIST_HTML)
        return httpx.Response(404, text="not found")

    return handler


def test_fetch_parses_latest_pmi() -> None:
    items = asyncio.run(_adapter(_handler()).fetch())
    assert len(items) == 1
    first = items[0]
    assert "49.2%" in first.title
    assert "2026-07" in first.title
    assert "下降1.1个百分点" in first.title
    assert first.external_id == "stats-pmi-2026-07"
    assert "t20260731" in first.url  # type: ignore[operator]
    assert first.extra.get("pmi") == "49.2"


def test_fetch_external_id_stable_per_month() -> None:
    ids1 = [it.external_id for it in asyncio.run(_adapter(_handler()).fetch())]
    ids2 = [it.external_id for it in asyncio.run(_adapter(_handler()).fetch())]
    assert ids1 == ids2


def test_healthcheck_ok_and_failure() -> None:
    health = asyncio.run(_adapter(_handler()).healthcheck())
    assert health.ok is True

    def no_list_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no pmi</html>")

    health2 = asyncio.run(_adapter(no_list_handler).healthcheck())
    assert health2.ok is False

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health3 = asyncio.run(_adapter(error_handler).healthcheck())
    assert health3.ok is False
