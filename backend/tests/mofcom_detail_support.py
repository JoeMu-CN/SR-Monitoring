"""MofcomEntityDetailAdapter 测试夹具与网络桩（不真实联网）。

供 ``test_sources_mofcom_detail.py`` 导入；独立成模块以控制测试文件规模。
所有场景都走"首页（transport 或 controlled_get）→ 详情（Crawl4AI 桩）"的
真实流程；不存在"把首页正文直接当详情 Markdown"的兼容捷径。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.signals import declarative as declarative_module
from app.signals import fallback as fallback_module
from app.signals import sources as sources_module
from app.signals.request_control import ControlledResponse, SourceRequestFailed

HOMEPAGE_URL = "http://aqygzj.mofcom.gov.cn/"
QUALIFYING_DETAIL_URL = "http://aqygzj.mofcom.gov.cn/art/2026/07/24/art_30.html"
SECOND_QUALIFYING_DETAIL_URL = "http://aqygzj.mofcom.gov.cn/art/2026/07/24/art_31.html"
NON_QUALIFYING_DETAIL_URL = "http://aqygzj.mofcom.gov.cn/art/2026/07/20/art_28.html"

QUALIFYING_TITLE = "商务部公告2026年第30号 公布将14家欧盟实体列入出口管制管控名单"
SECOND_QUALIFYING_TITLE = "商务部公告2026年第29号 将2家欧盟实体列入不可靠实体清单"
NON_QUALIFYING_TITLE = "关于举办出口管制业务培训的通知"

ENTITY_TITLES = [
    "出口管制名单新增：拉法特集团",
    "出口管制名单新增：太脱拉卡车公司",
    "出口管制名单新增：Opticoelectron集团",
]

DETAIL_MARKDOWN = """
商务部公告2026年第30号 公布将14家欧盟实体列入出口管制管控名单

根据《中华人民共和国出口管制法》等法律法规，决定将拉法特集团等14家欧盟实体
列入出口管制管控名单（见附件），并采取以下措施。

附件
出口管制管控名单
（2026年7月24日）

1.拉法特集团（Rafat Group）
地址：Smitweg 6, Kinderdijk, The Netherlands
邮编：2961

2.太脱拉卡车公司（TATRA TRUCKS a.s.）
地址：Areal Tatry 1450/1, Koprivnice, Czech Republic
邮编：74221

3.Opticoelectron集团（Opticoelectron Group）
地址：Industrial Park Opticoeletron, Panagyurishte, Bulgaria
邮编：4500

### 在线办事
  * [两用物项和技术进出口审批](http://www.mofcom.gov.cn/zwdt/lywxhjsjcksp/index.html)
"""

NON_ENTITY_MARKDOWN = """
来源：安全与管制局 类型：原创
2026-07-24 16:00
商务部公告2026年第30号 公布将14家欧盟实体列入出口管制管控名单

1.拉法特集团（Rafat Group）
2.太脱拉卡车公司（TATRA TRUCKS a.s.）

（完）
"""

# 抓取成功、但正文里没有任何"N.实体名"编号行的详情页。
NO_ENTITY_DETAIL_MARKDOWN = """
商务部公告2026年第30号 公布将14家欧盟实体列入出口管制管控名单

根据《中华人民共和国出口管制法》等法律法规，决定采取以下措施（完整名单见附件）。

（完）
"""

# 首页直连可恢复失败参数：(error_kind, http_status)。
RECOVERABLE_HOMEPAGE_ERRORS = [
    ("access_blocked", 403),
    ("rate_limited", 429),
    ("upstream_error", 502),
    ("network_error", None),
]


def html_anchor(href: str, inner_html: str, *, quote: str = '"') -> str:
    return f"<a href={quote}{href}{quote}>{inner_html}</a>"


def html_homepage(*anchors: str) -> str:
    return (
        '<html><body><ul class="list">'
        + "".join(f"<li>{anchor}</li>" for anchor in anchors)
        + "</ul></body></html>"
    )


HOMEPAGE_HTML = html_homepage(
    html_anchor(QUALIFYING_DETAIL_URL, QUALIFYING_TITLE),
    html_anchor(NON_QUALIFYING_DETAIL_URL, NON_QUALIFYING_TITLE),
)
NON_QUALIFYING_HTML = html_homepage(
    html_anchor(NON_QUALIFYING_DETAIL_URL, NON_QUALIFYING_TITLE)
)
HOMEPAGE_MARKDOWN = (
    f"* [{QUALIFYING_TITLE}]({QUALIFYING_DETAIL_URL})\n"
    f"* [{NON_QUALIFYING_TITLE}]({NON_QUALIFYING_DETAIL_URL})\n"
)
NON_QUALIFYING_MARKDOWN = f"* [{NON_QUALIFYING_TITLE}]({NON_QUALIFYING_DETAIL_URL})\n"
TWO_CANDIDATE_HTML = html_homepage(
    html_anchor(QUALIFYING_DETAIL_URL, QUALIFYING_TITLE),
    html_anchor(SECOND_QUALIFYING_DETAIL_URL, SECOND_QUALIFYING_TITLE),
)


def is_homepage(url: str) -> bool:
    return url.rstrip("/") == HOMEPAGE_URL.rstrip("/")


def no_entity_details() -> dict[str, str]:
    """两条候选详情都成功抓取但解析不出任何实体的 Crawl4AI 桩结果。"""
    return {
        QUALIFYING_DETAIL_URL: NO_ENTITY_DETAIL_MARKDOWN,
        SECOND_QUALIFYING_DETAIL_URL: NO_ENTITY_DETAIL_MARKDOWN,
    }


def direct_handler(
    *, status_code: int = 200, homepage_body: str = HOMEPAGE_HTML, log: list[str]
):
    """构造直连首页的 MockTransport handler；详情 URL 返回详情 markdown。"""

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(str(request.url))
        if is_homepage(str(request.url)):
            return httpx.Response(status_code, text=homepage_body)
        return httpx.Response(200, text=DETAIL_MARKDOWN)

    return handler


def patch_crawl4ai(
    monkeypatch,
    log: list[str],
    *,
    homepage: str = HOMEPAGE_MARKDOWN,
    details: dict[str, str | BaseException] | None = None,
) -> None:
    """替换 fallback.read_public_page_with_crawl4ai_for_monitor。"""

    async def _read(url: str, **_kwargs: object) -> str:
        log.append(url)
        if is_homepage(url):
            return homepage
        outcome = (details or {}).get(url)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return outcome
        return DETAIL_MARKDOWN

    monkeypatch.setattr(
        fallback_module, "read_public_page_with_crawl4ai_for_monitor", _read
    )


def patch_controlled_get(
    monkeypatch,
    log: list[str],
    *,
    status_code: int = 200,
    homepage_body: str = HOMEPAGE_HTML,
) -> None:
    """替换 sources.controlled_get：首页返回给定 HTML，其余返回详情 markdown。"""

    async def _get(url: str, **_kwargs: object) -> ControlledResponse:
        log.append(url)
        if is_homepage(url):
            return ControlledResponse(status_code, {}, homepage_body.encode("utf-8"))
        return ControlledResponse(200, {}, DETAIL_MARKDOWN.encode("utf-8"))

    monkeypatch.setattr(sources_module, "controlled_get", _get)


def patch_controlled_get_failure(
    monkeypatch,
    log: list[str],
    *,
    error_kind: str,
    status_code: int | None = None,
) -> None:
    """替换 sources.controlled_get：每次请求都抛 SourceRequestFailed。"""

    async def _get(url: str, **_kwargs: object) -> ControlledResponse:
        log.append(url)
        raise SourceRequestFailed(
            "直连首页请求失败", error_kind=error_kind, status_code=status_code
        )

    monkeypatch.setattr(sources_module, "controlled_get", _get)


class _BoomDeclarativeSourceAdapter:
    """一旦被实例化即失败：适配器不应再使用声明式列表源。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("不应实例化 DeclarativeSourceAdapter")


def guard_declarative_source(monkeypatch, calls: list[str]) -> None:
    """守住“不得依赖 source 4 声明式配置”这一约束。"""

    def _boom(transport: object = None) -> None:
        calls.append("mofcom-entity-control")
        raise AssertionError("不应读取 mofcom-entity-control（source 4）声明式配置")

    monkeypatch.setattr(sources_module, "_fetch_mofcom_list", _boom)
    monkeypatch.setattr(
        declarative_module, "DeclarativeSourceAdapter", _BoomDeclarativeSourceAdapter
    )


def patch_async_client(monkeypatch, log: list[str]) -> None:
    """兜底：目标若直接 new httpx.AsyncClient，也走 MockTransport，不真实联网。"""

    real_async_client = httpx.AsyncClient

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_async_client(
            *args,
            transport=httpx.MockTransport(direct_handler(log=log)),
            **kwargs,
        )

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@dataclass(slots=True)
class MofcomFetchEnv:
    """Mofcom 采集测试环境：已装配的适配器与三类调用记录。"""

    adapter: sources_module.MofcomEntityDetailAdapter
    direct_calls: list[str] = field(default_factory=list)
    crawl_calls: list[str] = field(default_factory=list)
    source_calls: list[str] = field(default_factory=list)


def controlled_env(
    monkeypatch,
    *,
    homepage_body: str = HOMEPAGE_HTML,
    homepage_markdown: str = HOMEPAGE_MARKDOWN,
    detail_markdowns: dict[str, str | BaseException] | None = None,
    guard: bool = False,
    async_client_fallback: bool = False,
) -> MofcomFetchEnv:
    """生产直连路径环境：controlled_get 与 Crawl4AI 均为桩，不真实联网。"""
    direct_calls: list[str] = []
    crawl_calls: list[str] = []
    source_calls: list[str] = []
    if guard:
        guard_declarative_source(monkeypatch, source_calls)
    patch_controlled_get(monkeypatch, direct_calls, homepage_body=homepage_body)
    patch_crawl4ai(
        monkeypatch, crawl_calls, homepage=homepage_markdown, details=detail_markdowns
    )
    if async_client_fallback:
        patch_async_client(monkeypatch, direct_calls)
    return MofcomFetchEnv(
        sources_module.MofcomEntityDetailAdapter(),
        direct_calls=direct_calls,
        crawl_calls=crawl_calls,
        source_calls=source_calls,
    )


def controlled_failure_env(
    monkeypatch, *, error_kind: str, status_code: int | None = None
) -> MofcomFetchEnv:
    """生产直连路径环境：首页请求必定抛 ``SourceRequestFailed``。"""
    direct_calls: list[str] = []
    crawl_calls: list[str] = []
    patch_controlled_get_failure(
        monkeypatch, direct_calls, error_kind=error_kind, status_code=status_code
    )
    patch_crawl4ai(monkeypatch, crawl_calls)
    return MofcomFetchEnv(
        sources_module.MofcomEntityDetailAdapter(),
        direct_calls=direct_calls,
        crawl_calls=crawl_calls,
    )


def detail_adapter(monkeypatch) -> sources_module.MofcomEntityDetailAdapter:
    """真实首页→详情流程：transport 供首页 HTML，Crawl4AI 桩供详情 Markdown。"""
    patch_crawl4ai(monkeypatch, [])
    return sources_module.MofcomEntityDetailAdapter(
        transport=httpx.MockTransport(direct_handler(log=[]))
    )
