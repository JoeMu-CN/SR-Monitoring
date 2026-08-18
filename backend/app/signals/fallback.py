"""监控轨 Crawl4AI 回退：直连受限/失败时用 Crawl4AI 容器重新抓取同一 URL。

设计原则：
- 只面向监控轨，与 ``app.research.web.read_public_page_with_crawl4ai`` 隔离；
  避免研究轨冻结配置被无意改动。
- 复用 ``app.config.MonitorCrawl4AISettings`` 单独 env 入口，避免污染研究轨。
- 输入校验与 ``app.signals.declarative.validate_public_https_url`` 一致，
  重复走一遍以防止调用方绕过直连层。
"""

from __future__ import annotations

import logging

import httpx

from app.config import MonitorCrawl4AISettings, get_monitor_crawl4ai_settings
from app.signals.declarative import validate_public_https_url
from app.signals.request_control import SourceRequestFailed
from app.signals.sources import SourceFetchError

logger = logging.getLogger(__name__)


async def read_public_page_with_crawl4ai_for_monitor(
    url: str,
    *,
    settings: MonitorCrawl4AISettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """通过 Crawl4AI 抓取单个 URL 并返回 Markdown 文本。

    与研究轨 ``read_public_page_with_crawl4ai`` 的差异：
    - 不返回 ``ResearchPageRead`` 包装对象，仅返回原始 markdown；
      监控轨适配器负责把它转回 HTML 节点供 ``_parse_rows`` 复用。
    - 接受可选 ``transport`` 参数便于测试：监控轨真实路径不传，
      由默认 httpx AsyncClient 走容器内网调 Crawl4AI。

    抛错：
    - ``SourceRequestFailed``（error_kind="crawler_unavailable"）：
      Crawl4AI 未配置或返回非 2xx。
    - ``SourceRequestFailed``（error_kind="network_error"）：
      与 Crawl4AI 的网络连接失败。
    """
    current = settings or get_monitor_crawl4ai_settings()
    if not current.enabled or not current.api_token:
        raise SourceRequestFailed(
            "监控轨 Crawl4AI 回退未配置", error_kind="crawler_unavailable"
        )
    # 同样要走公网 URL 校验：避免 Crawl4AI 出私网。
    try:
        await validate_public_https_url(url, resolve_dns=False)
    except SourceFetchError as exc:
        raise SourceRequestFailed(
            f"监控轨 Crawl4AI 回退目标 URL 非法: {exc}",
            error_kind="invalid_target",
        ) from exc
    endpoint = f"{current.base_url.rstrip('/')}/crawl"
    payload = {
        "urls": [url],
        "browser_config": {"headless": True},
        "crawler_config": {
            "cache_mode": "bypass",
            "word_count_threshold": 1,
            "excluded_tags": ["script", "style", "noscript", "template"],
        },
    }
    try:
        async with httpx.AsyncClient(
            timeout=current.timeout_seconds,
            trust_env=False,
            transport=transport,
            headers={
                "Authorization": f"Bearer {current.api_token}",
                "Content-Type": "application/json",
            },
        ) as client:
            response = await client.post(endpoint, json=payload)
    except httpx.HTTPError as exc:
        raise SourceRequestFailed(
            "监控轨 Crawl4AI 连接失败", error_kind="network_error"
        ) from exc
    if response.status_code >= 400:
        raise SourceRequestFailed(
            f"监控轨 Crawl4AI 返回 HTTP {response.status_code}",
            error_kind="crawler_unavailable",
            status_code=response.status_code,
        )
    try:
        payload_obj = response.json()
    except ValueError as exc:
        raise SourceRequestFailed(
            "监控轨 Crawl4AI 返回非 JSON",
            error_kind="crawler_unavailable",
        ) from exc
    # Crawl4AI /crawl 返回 {"results": [{"markdown": "..."}]} 或相似结构
    markdown = (
        (payload_obj.get("results") or [{}])[0].get("markdown")
        or payload_obj.get("markdown")
        or ""
    )
    if not markdown.strip():
        raise SourceRequestFailed(
            "监控轨 Crawl4AI 返回空 Markdown",
            error_kind="crawler_unavailable",
        )
    logger.warning("mem-incident-bulletin fallback path used url=%s", url)
    return markdown
