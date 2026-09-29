"""MofcomEntityDetailAdapter 显式 transport 测试脚手架（不真实联网）。

供 ``test_sources_mofcom_detail.py`` 导入；transport 供首页，Crawl4AI 桩供详情。
对 ``mofcom_detail_support`` 保持单向依赖，复用其夹具与桩，避免导入环。
"""

from __future__ import annotations

import httpx
from mofcom_detail_support import (
    HOMEPAGE_HTML,
    HOMEPAGE_MARKDOWN,
    MofcomFetchEnv,
    direct_handler,
    guard_declarative_source,
    patch_crawl4ai,
)

from app.signals import sources as sources_module


def connect_error_transport() -> httpx.MockTransport:
    """首页连接阶段抛 httpx.ConnectError 的 transport。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


def status_transport(status_code: int) -> httpx.MockTransport:
    """对任何请求都返回给定状态码的 transport（用于错误分类断言）。"""
    return httpx.MockTransport(lambda request: httpx.Response(status_code))


def transport_env(
    monkeypatch,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    status_code: int = 200,
    homepage_body: str = HOMEPAGE_HTML,
    homepage_markdown: str = HOMEPAGE_MARKDOWN,
    detail_markdowns: dict[str, str | BaseException] | None = None,
    guard: bool = False,
) -> MofcomFetchEnv:
    """显式 transport 环境：transport 供首页，Crawl4AI 桩供详情。"""
    direct_calls: list[str] = []
    crawl_calls: list[str] = []
    source_calls: list[str] = []
    if guard:
        guard_declarative_source(monkeypatch, source_calls)
    patch_crawl4ai(
        monkeypatch, crawl_calls, homepage=homepage_markdown, details=detail_markdowns
    )
    request_transport = transport
    if request_transport is None:
        request_transport = httpx.MockTransport(
            direct_handler(
                status_code=status_code, homepage_body=homepage_body, log=direct_calls
            )
        )
    return MofcomFetchEnv(
        sources_module.MofcomEntityDetailAdapter(transport=request_transport),
        direct_calls=direct_calls,
        crawl_calls=crawl_calls,
        source_calls=source_calls,
    )
