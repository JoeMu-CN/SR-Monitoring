"""研究轨受控单页读取。

该模块只负责一次研究来源的 HTTP 读取和证据摘要，不负责搜索、脚本执行或报告生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Crawl4AISettings, get_crawl4ai_settings
from app.signals.declarative import validate_public_https_url
from app.signals.request_control import (
    HostLease,
    PinnedIPTransport,
    SourceAccessDeferred,
    SourceRequestFailed,
    acquire_host_lease,
    classify_response,
    complete_host_lease,
    release_host_lease,
)
from app.signals.sources import SourceFetchError

MAX_RESEARCH_PAGE_BYTES = 1024 * 1024
MAX_RESEARCH_REDIRECTS = 3
MAX_RESEARCH_EXCERPT_CHARS = 12_000


@dataclass(frozen=True)
class ResearchPageRead:
    requested_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    status_code: int
    content_type: str
    excerpt: str
    reader: str = "direct_http"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0 and data.strip():
            self.parts.append(data)


async def _read_public_page_direct(
    url: str,
    *,
    maximum_bytes: int = MAX_RESEARCH_PAGE_BYTES,
    maximum_redirects: int = MAX_RESEARCH_REDIRECTS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ResearchPageRead:
    """读取单页并返回可审计的最终地址和正文摘要。

    每个重定向目标都会重新执行公网 HTTPS 校验；同域重定向复用当前页面租约，
    跨域时释放旧租约并为新域名重新申请。默认不使用环境代理，也不执行 JavaScript
    或跟随页面内链接。
    """
    if maximum_bytes < 1024 or maximum_bytes > MAX_RESEARCH_PAGE_BYTES:
        raise ValueError(f"maximum_bytes 必须在 1024 至 {MAX_RESEARCH_PAGE_BYTES} 之间")
    if maximum_redirects < 0 or maximum_redirects > 5:
        raise ValueError("maximum_redirects 必须在 0 至 5 之间")

    requested_url = url
    current_url = url
    redirect_chain: list[str] = []
    active_lease: HostLease | None = None
    active_transport: httpx.AsyncBaseTransport | None = transport
    try:
        for _ in range(maximum_redirects + 1):
            if active_transport is None:
                # 真实抓取：validate 阶段把公网 IP pin 住，避免 DNS rebinding
                pinned_ips = await validate_public_https_url(current_url, resolve_dns=True)
                parsed_current = urlparse(current_url)
                active_transport = PinnedIPTransport(
                    pinned_ips, parsed_current.hostname or ""
                )
            else:
                # 测试或上层传入的 transport：仅做轻量校验
                await validate_public_https_url(current_url, resolve_dns=False)
            response, active_lease = await _fetch_hop(
                current_url,
                maximum_bytes=maximum_bytes,
                transport=active_transport,
                lease=active_lease,
            )
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise SourceRequestFailed(
                        "源站重定向缺少 Location",
                        error_kind="redirect",
                        status_code=response.status_code,
                    )
                target = urljoin(current_url, location)
                if active_transport is None or not isinstance(active_transport, PinnedIPTransport):
                    # 上层 transport 不允许动态换主机；保持现状。
                    pass
                else:
                    # 为新的 target 重新解析并 pin
                    target_pinned = await validate_public_https_url(target, resolve_dns=True)
                    target_parsed = urlparse(target)
                    active_transport = PinnedIPTransport(
                        target_pinned, target_parsed.hostname or ""
                    )
                if _hostname(target) != _hostname(current_url) and active_lease:
                    release_host_lease(active_lease)
                    active_lease = None
                redirect_chain.append(target)
                current_url = target
                continue
            page = ResearchPageRead(
                requested_url=requested_url,
                final_url=current_url,
                redirect_chain=tuple(redirect_chain),
                status_code=response.status_code,
                content_type=content_type or "application/octet-stream",
                excerpt=_visible_excerpt(response.content, content_type),
                reader="direct_http",
            )
            if not page.excerpt:
                raise SourceRequestFailed("研究页面正文为空", error_kind="empty_content")
            return page
        raise SourceRequestFailed(
            f"重定向次数超过 {maximum_redirects} 次",
            error_kind="redirect_limit",
        )
    finally:
        if active_lease:
            release_host_lease(active_lease)


def _crawl4ai_result(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return results[0]
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


async def read_public_page_with_crawl4ai(
    url: str,
    *,
    settings: Crawl4AISettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ResearchPageRead:
    """通过已认证 Crawl4AI 读取单页；不允许深爬、脚本注入或任意配置透传。"""
    current = settings or get_crawl4ai_settings()
    if not current.enabled or not current.api_token:
        raise SourceRequestFailed("Crawl4AI 回退未配置", error_kind="crawler_unavailable")
    # Crawl4AI 内部抓取走它自己的浏览器，仍需确保目标 URL 解析为公网 IP；
    # 不在这里 pin transport，因为调用 Crawl4AI 服务是平台内网地址。
    await validate_public_https_url(url, resolve_dns=False)
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
            transport=transport,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {current.api_token}",
                "Content-Type": "application/json",
            },
        ) as client:
            response = await client.post(endpoint, json=payload)
    except httpx.HTTPError as exc:
        raise SourceRequestFailed(
            "Crawl4AI 网络请求失败", error_kind="crawler_network_error"
        ) from exc
    if response.status_code >= 400:
        raise SourceRequestFailed(
            "Crawl4AI 读取失败",
            error_kind="crawler_http_error",
            status_code=response.status_code,
        )
    try:
        result = _crawl4ai_result(response.json())
    except ValueError as exc:
        raise SourceRequestFailed(
            "Crawl4AI 返回非 JSON", error_kind="crawler_invalid_response"
        ) from exc
    if result is None:
        raise SourceRequestFailed("Crawl4AI 没有返回页面结果", error_kind="crawler_empty_result")
    excerpt = str(
        result.get("markdown")
        or result.get("cleaned_markdown")
        or result.get("fit_markdown")
        or result.get("raw_markdown")
        or result.get("html")
        or ""
    ).strip()
    if "<html" in excerpt.lower() or "<body" in excerpt.lower():
        excerpt = _visible_excerpt(excerpt.encode("utf-8"), "text/html")
    else:
        excerpt = " ".join(excerpt.split())[:MAX_RESEARCH_EXCERPT_CHARS]
    if not excerpt:
        raise SourceRequestFailed("Crawl4AI 返回空正文", error_kind="empty_content")
    final_url = str(result.get("url") or result.get("final_url") or url)
    status_value = result.get("status_code") or result.get("status") or 200
    try:
        status_code = int(status_value) if isinstance(status_value, (int, str)) else 200
    except (TypeError, ValueError):
        status_code = 200
    return ResearchPageRead(
        requested_url=url,
        final_url=final_url,
        redirect_chain=(),
        status_code=status_code,
        content_type="text/markdown",
        excerpt=excerpt,
        reader="crawl4ai",
    )


async def read_public_page(
    url: str,
    *,
    maximum_bytes: int = MAX_RESEARCH_PAGE_BYTES,
    maximum_redirects: int = MAX_RESEARCH_REDIRECTS,
    transport: httpx.AsyncBaseTransport | None = None,
    use_crawl4ai_fallback: bool = True,
) -> ResearchPageRead:
    """先直连读取，失败时按配置回退到 Crawl4AI 单页渲染。"""
    try:
        return await _read_public_page_direct(
            url,
            maximum_bytes=maximum_bytes,
            maximum_redirects=maximum_redirects,
            transport=transport,
        )
    except (SourceRequestFailed, SourceFetchError, ValueError, RuntimeError) as error:
        settings = get_crawl4ai_settings()
        if (
            not use_crawl4ai_fallback
            or not settings.enabled
            or not settings.api_token
            or not _crawl4ai_fallback_allowed(error)
        ):
            raise
        return await read_public_page_with_crawl4ai(
            url,
            settings=settings,
            transport=transport,
        )


def _crawl4ai_fallback_allowed(error: BaseException) -> bool:
    """仅对网络/上游故障或空正文回退，不用浏览器绕过 WAF、限流和认证。"""
    error_kind = getattr(error, "error_kind", None)
    return error_kind in {"network_error", "upstream_error", "empty_content"}


@dataclass(frozen=True)
class _HopResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes


async def _fetch_hop(
    url: str,
    *,
    maximum_bytes: int,
    transport: httpx.AsyncBaseTransport | None,
    lease: HostLease | None = None,
) -> tuple[_HopResponse, HostLease | None]:
    lease_completed = False
    if transport is None and lease is None:
        try:
            lease = acquire_host_lease(url)
        except SourceAccessDeferred as exc:
            raise SourceRequestFailed(str(exc), error_kind="deferred") from exc
    try:
        async with httpx.AsyncClient(
            timeout=15,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", url) as response:
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > maximum_bytes:
                    raise SourceRequestFailed(
                        f"研究页面响应超过 {maximum_bytes} 字节限制",
                        error_kind="response_too_large",
                        status_code=response.status_code,
                    )
                content = await response.aread()
                if len(content) > maximum_bytes:
                    raise SourceRequestFailed(
                        f"研究页面响应超过 {maximum_bytes} 字节限制",
                        error_kind="response_too_large",
                        status_code=response.status_code,
                    )
                headers = dict(response.headers)
                decision = classify_response(
                    response.status_code,
                    headers,
                    content.decode("utf-8", errors="replace"),
                )
                is_redirect = response.status_code in {301, 302, 303, 307, 308}
                if lease and (not is_redirect or decision.error_kind):
                    complete_host_lease(
                        lease,
                        status_code=response.status_code,
                        decision=decision,
                    )
                    lease_completed = True
                if decision.error_kind:
                    raise SourceRequestFailed(
                        decision.message or "研究来源拒绝请求",
                        error_kind=decision.error_kind,
                        status_code=response.status_code,
                    )
                return (
                    _HopResponse(response.status_code, headers, content),
                    lease if is_redirect else None,
                )
    except SourceRequestFailed:
        if lease and not lease_completed:
            release_host_lease(lease)
        raise
    except httpx.HTTPError as exc:
        if lease:
            complete_host_lease(lease, network_error="研究页面网络请求失败")
        raise SourceRequestFailed("研究页面网络请求失败", error_kind="network_error") from exc
    except Exception:
        if lease and not lease_completed:
            release_host_lease(lease)
        raise


def _visible_excerpt(content: bytes, content_type: str) -> str:
    text = content.decode("utf-8-sig", errors="replace")
    if "html" in content_type or re.search(r"<html\b|<body\b", text, re.I):
        parser = _VisibleTextParser()
        parser.feed(text)
        text = " ".join(parser.parts)
    return " ".join(text.split())[:MAX_RESEARCH_EXCERPT_CHARS]


def _hostname(url: str) -> str:
    hostname = (urlparse(url).hostname or "").rstrip(".").lower()
    if not hostname:
        raise ValueError("研究页面 URL 缺少有效域名")
    return hostname
