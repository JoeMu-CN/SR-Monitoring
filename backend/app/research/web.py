"""研究轨受控单页读取。

该模块只负责一次研究来源的 HTTP 读取和证据摘要，不负责搜索、脚本执行或报告生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from app.signals.declarative import validate_public_https_url
from app.signals.request_control import (
    HostLease,
    SourceAccessDeferred,
    SourceRequestFailed,
    acquire_host_lease,
    classify_response,
    complete_host_lease,
    release_host_lease,
)

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


async def read_public_page(
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
    try:
        for _ in range(maximum_redirects + 1):
            await validate_public_https_url(current_url, resolve_dns=transport is None)
            response, active_lease = await _fetch_hop(
                current_url,
                maximum_bytes=maximum_bytes,
                transport=transport,
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
                await validate_public_https_url(target, resolve_dns=transport is None)
                if _hostname(target) != _hostname(current_url) and active_lease:
                    release_host_lease(active_lease)
                    active_lease = None
                redirect_chain.append(target)
                current_url = target
                continue
            return ResearchPageRead(
                requested_url=requested_url,
                final_url=current_url,
                redirect_chain=tuple(redirect_chain),
                status_code=response.status_code,
                content_type=content_type or "application/octet-stream",
                excerpt=_visible_excerpt(response.content, content_type),
            )
        raise SourceRequestFailed(
            f"重定向次数超过 {maximum_redirects} 次",
            error_kind="redirect_limit",
        )
    finally:
        if active_lease:
            release_host_lease(active_lease)


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
