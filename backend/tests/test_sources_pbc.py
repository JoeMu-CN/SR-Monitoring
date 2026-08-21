"""PbcLprAdapter（央行 LPR 报价，E4 货币政策）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import PbcLprAdapter

_LIST_HTML = (
    "<html><body>\n"
    "<h1>利率</h1>\n"
    "<ul>\n"
    "<li><a href=\"/zhengcehuobisi/125207/125213/125440/3876551/"
    "2026082008353223017/index.html\">2026年8月20日全国银行间同业拆借中心"
    "受权公布贷款市场报价利率（LPR）公告</a></li>\n"
    "<li><a href=\"/zhengcehuobisi/125207/125213/125440/3876551/"
    "2026072008093186869/index.html\">2026年7月20日全国银行间同业拆借中心"
    "受权公布贷款市场报价利率（LPR）公告</a></li>\n"
    "</ul>\n"
    "</body></html>\n"
)

_DETAIL_HTML = """<html><body>
<div id="zoom" class="zoom1">
中国人民银行授权全国银行间同业拆借中心公布，2026年8月20日贷款市场报价利率（LPR）为：1年期LPR为3.0%，5年期以上LPR为3.5%。以上LPR在下一次发布LPR之前有效。
</div>
</body></html>
"""


def _adapter(handler) -> PbcLprAdapter:
    return PbcLprAdapter(transport=httpx.MockTransport(handler))


def _handler():
    def handler(request: httpx.Request) -> httpx.Response:
        if "125440/index.html" in str(request.url):
            return httpx.Response(200, text=_LIST_HTML)
        if "2026082008353223017" in str(request.url):
            return httpx.Response(200, text=_DETAIL_HTML)
        return httpx.Response(404, text="not found")

    return handler


def test_fetch_parses_latest_lpr() -> None:
    items = asyncio.run(_adapter(_handler()).fetch())
    assert len(items) == 1
    first = items[0]
    assert "3.0%" in first.title
    assert "3.5%" in first.title
    assert "2026-08-20" in first.title
    assert first.external_id == "pbc-lpr-2026-08-20"
    assert "2026082008353223017" in first.url  # type: ignore[operator]
    assert first.extra.get("lpr_1y") == "3.0"
    assert first.extra.get("lpr_5y") == "3.5"


def test_fetch_external_id_stable_per_month() -> None:
    ids1 = [it.external_id for it in asyncio.run(_adapter(_handler()).fetch())]
    ids2 = [it.external_id for it in asyncio.run(_adapter(_handler()).fetch())]
    assert ids1 == ids2  # 同月幂等


def test_healthcheck_ok_and_failure() -> None:
    health = asyncio.run(_adapter(_handler()).healthcheck())
    assert health.ok is True

    def no_list_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no announcements</html>")

    health2 = asyncio.run(_adapter(no_list_handler).healthcheck())
    assert health2.ok is False

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health3 = asyncio.run(_adapter(error_handler).healthcheck())
    assert health3.ok is False
