"""中央气象台公告类栏目适配器测试（MockTransport，不访问真实网络）。"""

import asyncio
from collections.abc import Callable

import httpx
import pytest

from app.signals.sources import NmcBulletinAdapter, SourceFetchError
from app.signals.validity import ValidityProfile

PERDAY = ("perday", "每日天气提示", "https://www.nmc.cn/publish/weatherperday/index.htm")
TYPHOON = (
    "typhoon",
    "台风预警",
    "https://www.nmc.cn/publish/country/warning/typhoon.html",
)


def _page(lines: list[str]) -> str:
    """复刻栏目页结构：#text 内是公告正文，容器外是导航与页脚噪声。"""
    inner = "".join(f"<p>{line}</p>" for line in lines)
    return (
        "<html><head><title>天气预报</title></head><body>"
        "<div class='nav'><p>导航：首页 天气实况 城市预报</p></div>"
        f"<div id=text>{inner}</div>"
        "<div id=footer><p>页脚：中国气象局</p></div>"
        "</body></html>"
    )


PERDAY_HTML = _page(
    [
        "每 日 天 气 提 示",
        "制作：刘璐&nbsp;&nbsp;签发：王冠岚&nbsp;&nbsp;2026 年 10 月 22 日 08 时",
        "台风“蔷琵”将影响华东沿海 新疆有大范围降温雨雪天气",
        "气象监测显示，昨日新疆北部等地出现小到中雪或雨夹雪，局地大雪。",
    ]
)

TYPHOON_HTML = _page(
    [
        "台风预警",
        "预报：李晓兰&nbsp;&nbsp;签发：向纯怡&nbsp;&nbsp;2026 年 10 月 22 日 10 时",
        "中央气象台10月22日10时继续发布台风蓝色预警：",
        "今年第30号台风“蔷琵”的中心位于台湾省台北市东南方向约710公里。",
    ]
)


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    sections: tuple[tuple[str, str, str], ...] = (PERDAY, TYPHOON),
) -> NmcBulletinAdapter:
    return NmcBulletinAdapter(transport=httpx.MockTransport(handler), sections=sections)


def _router(*pages: str) -> Callable[[httpx.Request], httpx.Response]:
    mapping = {
        PERDAY[2]: pages[0],
        TYPHOON[2]: pages[1] if len(pages) > 1 else pages[0],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=mapping[str(request.url)])

    return handler


def test_fetch_parses_each_section_latest_article() -> None:
    adapter = _adapter(_router(PERDAY_HTML, TYPHOON_HTML))
    items = asyncio.run(adapter.fetch())

    assert [item.external_id for item in items] == [
        "perday-2026102208",
        "typhoon-2026102210",
    ]
    perday = items[0]
    assert perday.title == "台风“蔷琵”将影响华东沿海 新疆有大范围降温雨雪天气"
    assert perday.published_at is not None
    # 公告发布时刻是北京时间，落库统一为 UTC。
    assert perday.published_at.isoformat() == "2026-10-22T00:00:00+00:00"
    assert perday.validity_profile is ValidityProfile.WEATHER_ALERT
    assert perday.extra["content_kind"] == "announcement"
    assert perday.extra["section"] == "perday"
    # 正文只取 #text 容器，导航与页脚噪声不得混入。
    assert "导航" not in perday.content and "页脚" not in perday.content
    assert "气象监测显示" in perday.content
    assert perday.url == PERDAY[2]

    typhoon = items[1]
    # 抬头行（栏目名/预报签发行）跳过，标题为发布预警的首句且去掉尾部冒号。
    assert typhoon.title == "中央气象台10月22日10时继续发布台风蓝色预警"
    assert typhoon.published_at is not None
    assert typhoon.published_at.isoformat() == "2026-10-22T02:00:00+00:00"


def test_single_section_failure_keeps_other_sections() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TYPHOON[2]:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, text=PERDAY_HTML)

    items = asyncio.run(_adapter(handler).fetch())
    assert [item.extra["section"] for item in items] == ["perday"]


def test_all_sections_failing_raises_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>结构变更</body></html>")

    with pytest.raises(SourceFetchError):
        asyncio.run(_adapter(handler).fetch())


def test_fetch_raises_when_body_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page(["每日天气提示"]))

    with pytest.raises(SourceFetchError):
        asyncio.run(_adapter(handler, sections=(PERDAY,)).fetch())


def test_content_is_capped() -> None:
    long_html = _page(
        [
            "重要天气提示",
            "2026 年 10 月 20 日 15 时",
            "台风“蔷琵”趋向台湾东部沿海",
        ]
        + ["正文段落" * 50] * 40
    )
    item = asyncio.run(
        _adapter(
            lambda request: httpx.Response(200, text=long_html), sections=(PERDAY,)
        ).fetch()
    )[0]
    assert len(item.content) == NmcBulletinAdapter.MAX_ARTICLE_CHARS


def test_normalize_and_fingerprint_are_stable() -> None:
    adapter = NmcBulletinAdapter()
    item = asyncio.run(
        _adapter(_router(PERDAY_HTML, TYPHOON_HTML), sections=(PERDAY,)).fetch()
    )[0]
    signal = adapter.normalize(item)
    assert signal.external_id == "perday-2026102208"
    assert adapter.fingerprint(signal) == adapter.fingerprint(adapter.normalize(item))


def test_healthcheck_reports_section_count() -> None:
    health = asyncio.run(_adapter(_router(PERDAY_HTML, TYPHOON_HTML)).healthcheck())
    assert health.ok is True
    assert health.message == "返回 2 条公告（共 2 个栏目）"


def test_healthcheck_failed_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    health = asyncio.run(_adapter(handler).healthcheck())
    assert health.ok is False
