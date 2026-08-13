"""研究轨搜索 Provider 的最小协议和预算闸门。

本模块定义搜索结果契约、可选商业 Provider 和 Mock Provider。
搜索结果本身不构成证据，必须交给受控单页读取器进一步校验。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.config import SearchSettings, get_search_settings

MAX_QUERY_CHARS = 500
MAX_RESULTS_PER_QUERY = 20
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SearchProviderError(RuntimeError):
    """搜索服务失败或返回了不可用结果。"""


class SearchQueryError(SearchProviderError):
    """查询不符合公开信息搜索边界。"""


class SearchBudgetExceeded(SearchProviderError):
    """搜索任务达到查询次数或候选结果预算。"""


TAVILY_ENDPOINT = "https://api.tavily.com/search"
BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"


def build_configured_search_provider(
    settings: SearchSettings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SearchProvider | None:
    """按环境配置构造真实 Provider；未配置时返回 None，不发起网络请求。"""
    current = settings or get_search_settings()
    if current.provider in {"", "none", "fake"} or not current.api_key:
        return None
    if current.provider == "tavily":
        return TavilySearchProvider(
            api_key=current.api_key,
            endpoint=current.base_url or TAVILY_ENDPOINT,
            timeout_seconds=current.timeout_seconds,
            transport=transport,
        )
    if current.provider == "bocha":
        return BochaSearchProvider(
            api_key=current.api_key,
            endpoint=current.base_url or BOCHA_ENDPOINT,
            timeout_seconds=current.timeout_seconds,
            transport=transport,
        )
    raise SearchProviderError(f"不支持的搜索 Provider：{current.provider}")


@dataclass(frozen=True)
class SearchCandidate:
    url: str
    title: str
    snippet: str
    published_at: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    provider_name: str
    query: str
    results: tuple[SearchCandidate, ...]


class SearchProvider(Protocol):
    provider_name: str

    async def search(self, query: str, *, max_results: int) -> SearchResponse: ...


class TavilySearchProvider:
    provider_name = "tavily"

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = TAVILY_ENDPOINT,
        timeout_seconds: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._endpoint = _validate_provider_endpoint(endpoint)
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        normalized = normalize_search_query(query)
        if max_results < 1 or max_results > MAX_RESULTS_PER_QUERY:
            raise ValueError(f"max_results 必须在 1 至 {MAX_RESULTS_PER_QUERY} 之间")
        payload = {
            "api_key": self._api_key,
            "query": normalized,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
            trust_env=False,
        ) as client:
            try:
                response = await client.post(self._endpoint, json=payload)
            except httpx.HTTPError as exc:
                raise SearchProviderError("Tavily 搜索网络请求失败") from exc
        if response.status_code >= 400:
            raise SearchProviderError(f"Tavily 搜索失败：HTTP {response.status_code}")
        return _response_from_items(
            self.provider_name,
            normalized,
            response,
            items_path=("results",),
            max_results=max_results,
        )


class BochaSearchProvider:
    provider_name = "bocha"

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = BOCHA_ENDPOINT,
        timeout_seconds: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._endpoint = _validate_provider_endpoint(endpoint)
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        normalized = normalize_search_query(query)
        if max_results < 1 or max_results > MAX_RESULTS_PER_QUERY:
            raise ValueError(f"max_results 必须在 1 至 {MAX_RESULTS_PER_QUERY} 之间")
        payload = {
            "query": normalized,
            "freshness": "noLimit",
            "summary": True,
            "count": max_results,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
            trust_env=False,
            headers={"Authorization": f"Bearer {self._api_key}"},
        ) as client:
            try:
                response = await client.post(self._endpoint, json=payload)
            except httpx.HTTPError as exc:
                raise SearchProviderError("Bocha 搜索网络请求失败") from exc
        if response.status_code >= 400:
            raise SearchProviderError(f"Bocha 搜索失败：HTTP {response.status_code}")
        return _response_from_items(
            self.provider_name,
            normalized,
            response,
            items_path=("data", "webPages", "value"),
            max_results=max_results,
        )


def _validate_provider_endpoint(endpoint: str) -> str:
    normalized = endpoint.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SearchProviderError("搜索 Provider 端点仅允许绝对 HTTPS URL")
    if parsed.username or parsed.password:
        raise SearchProviderError("搜索 Provider 端点不允许包含用户凭据")
    _sanitize_result_url(normalized)
    return normalized


def _response_from_items(
    provider_name: str,
    query: str,
    response: httpx.Response,
    *,
    items_path: tuple[str, ...],
    max_results: int,
) -> SearchResponse:
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise SearchProviderError(f"{provider_name} 搜索返回非 JSON") from exc
    current = payload
    for key in items_path:
        if not isinstance(current, dict):
            raise SearchProviderError(f"{provider_name} 搜索响应结构无效")
        current = current.get(key)
    if not isinstance(current, list):
        raise SearchProviderError(f"{provider_name} 搜索响应缺少结果列表")
    candidates: list[SearchCandidate] = []
    for item in current:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        candidates.append(
            SearchCandidate(
                url=url,
                title=str(item.get("title") or item.get("name") or ""),
                snippet=str(item.get("content") or item.get("snippet") or ""),
                published_at=(
                    str(item.get("published_date") or item.get("datePublished"))
                    if item.get("published_date") or item.get("datePublished")
                    else None
                ),
            )
        )
        if len(candidates) >= max_results:
            break
    return _sanitize_response(
        SearchResponse(provider_name, query, tuple(candidates)),
        query,
        max_results,
    )


@dataclass
class SearchBudget:
    """单个研究任务的确定性搜索预算；不负责金额换算。"""

    max_queries: int = 5
    max_results: int = 50
    queries_used: int = 0
    results_used: int = 0

    def __post_init__(self) -> None:
        if self.max_queries < 1 or self.max_results < 1:
            raise ValueError("搜索预算上限必须大于 0")
        if self.queries_used < 0 or self.results_used < 0:
            raise ValueError("搜索预算已用量不能为负数")

    def reserve(self, requested_results: int) -> int:
        if requested_results < 1:
            raise ValueError("requested_results 必须大于 0")
        if self.queries_used >= self.max_queries:
            raise SearchBudgetExceeded("研究任务已达到搜索次数上限")
        if self.results_used >= self.max_results:
            raise SearchBudgetExceeded("研究任务已达到搜索结果上限")
        allowed = min(
            requested_results,
            MAX_RESULTS_PER_QUERY,
            self.max_results - self.results_used,
        )
        self.queries_used += 1
        self.results_used += allowed
        return allowed


async def run_search(
    provider: SearchProvider,
    query: str,
    *,
    budget: SearchBudget,
    max_results: int = 10,
) -> SearchResponse:
    """在查询校验和预算扣减后调用 Provider。

    预算先扣减再发请求，调用失败也不会自动重试或切换第二 Provider，避免重复计费。
    """
    normalized_query = normalize_search_query(query)
    allowed = budget.reserve(max_results)
    response = await provider.search(normalized_query, max_results=allowed)
    return _sanitize_response(response, normalized_query, allowed)


def normalize_search_query(query: str) -> str:
    if _CONTROL_CHARS.search(query):
        raise SearchQueryError("搜索查询包含控制字符")
    normalized = " ".join(query.split())
    if not normalized:
        raise SearchQueryError("搜索查询不能为空")
    if len(normalized) > MAX_QUERY_CHARS:
        raise SearchQueryError(f"搜索查询不能超过 {MAX_QUERY_CHARS} 个字符")
    return normalized


@dataclass
class FakeSearchProvider:
    """只用于协议测试的确定性 Provider，不执行网络请求。"""

    responses: dict[str, tuple[SearchCandidate, ...]] = field(default_factory=dict)
    provider_name: str = "fake"
    calls: list[str] = field(default_factory=list)

    async def search(self, query: str, *, max_results: int) -> SearchResponse:
        if max_results < 1 or max_results > MAX_RESULTS_PER_QUERY:
            raise ValueError(f"max_results 必须在 1 至 {MAX_RESULTS_PER_QUERY} 之间")
        normalized_query = normalize_search_query(query)
        self.calls.append(normalized_query)
        candidates = self.responses.get(normalized_query, ())
        return SearchResponse(
            provider_name=self.provider_name,
            query=normalized_query,
            results=tuple(candidates[:max_results]),
        )


def _sanitize_response(
    response: SearchResponse,
    query: str,
    max_results: int,
) -> SearchResponse:
    if response.provider_name.strip() == "":
        raise SearchProviderError("搜索响应缺少 Provider 名称")
    unique: list[SearchCandidate] = []
    seen: set[str] = set()
    for candidate in response.results:
        try:
            url = _sanitize_result_url(candidate.url)
        except SearchProviderError:
            # Provider 可能返回 HTTP、内网或带凭据的候选；丢弃该条，
            # 但保留同一响应中的其他合法 HTTPS 公网结果。
            continue
        if url in seen:
            continue
        seen.add(url)
        unique.append(
            SearchCandidate(
                url=url,
                title=" ".join(candidate.title.split())[:500],
                snippet=" ".join(candidate.snippet.split())[:2000],
                published_at=candidate.published_at,
            )
        )
        if len(unique) >= max_results:
            break
    if response.results and not unique:
        raise SearchProviderError("搜索响应没有可用的 HTTPS 公网结果")
    return SearchResponse(response.provider_name, query, tuple(unique))


def _sanitize_result_url(url: str) -> str:
    normalized = url.strip()
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme != "https" or not hostname:
        raise SearchProviderError("搜索结果仅允许绝对 HTTPS URL")
    if parsed.username or parsed.password:
        raise SearchProviderError("搜索结果 URL 不允许包含用户凭据")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise SearchProviderError("搜索结果禁止指向本机或内部网络")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise SearchProviderError("搜索结果禁止指向非公网 IP 地址")
    return normalized
