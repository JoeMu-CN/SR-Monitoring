"""MeeAnnouncementAdapter（生态环境部公告，P1 行业监管）单测。"""

from __future__ import annotations

import asyncio

import httpx

from app.signals.sources import MeeAnnouncementAdapter

# 模拟生态环境部首页（含环评审批/标准公告 + 无关新闻 + 导航）
_HTML = (
    "<html><body>\n<ul class='index_ywTab'>\n"
    "<li><a href='./ywdt/gsgg/tz/202608/t20260821_1164326.shtml' target='_blank'>"
    "生态环境部关于2026年8月18日作出的建设项目环境影响评价文件审批决定的公告（核与辐射）</a></li>\n"
    "<li><a href='./ywdt/gsgg/tz/202608/t20260807_1163728.html' target='_blank'>"
    "关于发布国家生态环境标准《海洋倾倒区选划技术导则》的公告</a></li>\n"
    "<li><a href='./ywdt/xwfb/202608/t20260813_1164004.shtml' target='_blank'>"
    "生态环境部自然生态保护司有关负责人就《生态保护“十五五”规划》答记者问</a></li>\n"
    "<li><a href='./ywdt/xwfb/202607/t20260729_1163032.shtml' target='_blank'>"
    "7月例行新闻发布会答问实录</a></li>\n"
    "</ul>\n</body></html>\n"
)


def _adapter(handler) -> MeeAnnouncementAdapter:
    return MeeAnnouncementAdapter(transport=httpx.MockTransport(handler))


def test_fetch_filters_to_regulatory_announcements() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    items = asyncio.run(_adapter(handler).fetch())
    # 只保留命中监管关键词的（环评审批 + 标准），过滤新闻发布/答记者问
    assert len(items) == 2
    titles = {it.title for it in items}
    assert any("环境影响评价" in t for t in titles)
    assert any("标准" in t for t in titles)
    assert not any("答记者问" in t for t in titles)
    assert not any("新闻发布会" in t for t in titles)
    # URL 补全为绝对地址
    eia = [it for it in items if "环境影响评价" in it.title][0]
    assert eia.url.startswith(  # type: ignore[union-attr]
        "https://www.mee.gov.cn/ywdt/gsgg/tz/"
    )
    assert eia.published_at is not None
    assert eia.published_at.year == 2026
    assert eia.external_id.startswith("mee-")


def test_fetch_external_id_stable_for_dedupe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    ids1 = {it.external_id for it in asyncio.run(_adapter(handler).fetch())}
    ids2 = {it.external_id for it in asyncio.run(_adapter(handler).fetch())}
    assert ids1 == ids2
    assert len(ids1) == 2


def test_healthcheck_ok_and_failure() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    health = asyncio.run(_adapter(ok_handler).healthcheck())
    assert health.ok is True

    def no_match_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>only news, no regulation</html>")

    health2 = asyncio.run(_adapter(no_match_handler).healthcheck())
    assert health2.ok is False

    def error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health3 = asyncio.run(_adapter(error_handler).healthcheck())
    assert health3.ok is False
