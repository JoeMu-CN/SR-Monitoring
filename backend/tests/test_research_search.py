"""研究轨搜索 Provider 协议测试（不访问真实网络）。"""

import asyncio

import httpx
import pytest

from app.config import SearchSettings
from app.research.search import (
    BochaSearchProvider,
    FakeSearchProvider,
    SearchBudget,
    SearchBudgetExceeded,
    SearchCandidate,
    SearchProviderError,
    SearchQueryError,
    TavilySearchProvider,
    build_configured_search_provider,
    run_search,
)


def _run(coro):
    return asyncio.run(coro)


def test_run_search_normalizes_deduplicates_and_caps_results() -> None:
    provider = FakeSearchProvider(
        responses={
            "公开主题": (
                SearchCandidate("https://official.example/a", "  标题 A ", " 摘要 A "),
                SearchCandidate("https://official.example/a", "重复", "重复"),
                SearchCandidate("https://official.example/b", "标题 B", "摘要 B"),
            )
        }
    )
    budget = SearchBudget(max_queries=2, max_results=5)

    result = _run(run_search(provider, "  公开主题\n", budget=budget, max_results=3))

    assert provider.calls == ["公开主题"]
    assert result.query == "公开主题"
    assert [item.url for item in result.results] == [
        "https://official.example/a",
        "https://official.example/b",
    ]
    assert result.results[0].title == "标题 A"
    assert budget.queries_used == 1
    assert budget.results_used == 3


@pytest.mark.parametrize("query", ["", "   ", "x\x00y", "x" * 501])
def test_run_search_rejects_invalid_query(query: str) -> None:
    provider = FakeSearchProvider()
    with pytest.raises(SearchQueryError):
        _run(run_search(provider, query, budget=SearchBudget()))
    assert provider.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://official.example/page",
        "https://127.0.0.1/page",
        "https://user:pass@official.example/page",
        "https://service.internal/page",
    ],
)
def test_run_search_rejects_unsafe_candidate_url(url: str) -> None:
    provider = FakeSearchProvider(
        responses={"主题": (SearchCandidate(url, "标题", "摘要"),)}
    )
    with pytest.raises(SearchProviderError):
        _run(run_search(provider, "主题", budget=SearchBudget()))


def test_run_search_skips_unsafe_candidates_and_keeps_public_https_results() -> None:
    provider = FakeSearchProvider(
        responses={
            "主题": (
                SearchCandidate("http://official.example/old", "不安全", "不安全"),
                SearchCandidate("https://official.example/a", "标题 A", "摘要 A"),
                SearchCandidate("https://127.0.0.1/private", "内网", "内网"),
                SearchCandidate("https://official.example/b", "标题 B", "摘要 B"),
            )
        }
    )

    result = _run(run_search(provider, "主题", budget=SearchBudget()))

    assert [item.url for item in result.results] == [
        "https://official.example/a",
        "https://official.example/b",
    ]


def test_run_search_budget_blocks_second_query_and_does_not_retry() -> None:
    provider = FakeSearchProvider(responses={"主题": ()})
    budget = SearchBudget(max_queries=1, max_results=2)

    _run(run_search(provider, "主题", budget=budget, max_results=2))
    with pytest.raises(SearchBudgetExceeded):
        _run(run_search(provider, "主题", budget=budget, max_results=2))

    assert provider.calls == ["主题"]


def test_run_search_budget_caps_total_results_across_queries() -> None:
    provider = FakeSearchProvider(responses={"一": (), "二": ()})
    budget = SearchBudget(max_queries=3, max_results=3)

    first = _run(run_search(provider, "一", budget=budget, max_results=2))
    second = _run(run_search(provider, "二", budget=budget, max_results=10))

    assert first.results == ()
    assert second.results == ()
    assert budget.queries_used == 2
    assert budget.results_used == 3


def test_search_provider_is_not_constructed_without_configuration() -> None:
    provider = build_configured_search_provider(
        SearchSettings(provider="none", api_key="", base_url="", timeout_seconds=15)
    )
    assert provider is None


def test_search_provider_rejects_private_custom_endpoint() -> None:
    with pytest.raises(SearchProviderError):
        TavilySearchProvider(api_key="test-only-key", endpoint="https://127.0.0.1/search")


def test_tavily_provider_maps_public_results_without_leaking_key() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers.get("authorization")
        observed["payload"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://official.example/a",
                        "title": "标题",
                        "content": "摘要",
                        "published_date": "2026-08-11",
                    }
                ]
            },
            request=request,
        )

    provider = TavilySearchProvider(
        api_key="test-only-key",
        endpoint="https://search.test/tavily",
        transport=httpx.MockTransport(handler),
    )
    response = _run(provider.search("公开主题", max_results=3))

    assert response.provider_name == "tavily"
    assert response.results[0].url == "https://official.example/a"
    assert response.results[0].published_at == "2026-08-11"
    assert observed["authorization"] is None
    assert "test-only-key" in str(observed["payload"])


def test_bocha_provider_maps_nested_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-only-key"
        return httpx.Response(
            200,
            json={
                "data": {
                    "webPages": {
                        "value": [
                            {
                                "url": "http://official.example/insecure",
                                "name": "应被丢弃",
                                "snippet": "应被丢弃",
                            },
                            {
                                "url": "https://official.example/b",
                                "name": "标题 B",
                                "snippet": "摘要 B",
                                "datePublished": "2026-08-10",
                            }
                        ]
                    }
                }
            },
            request=request,
        )

    provider = BochaSearchProvider(
        api_key="test-only-key",
        endpoint="https://search.test/bocha",
        transport=httpx.MockTransport(handler),
    )
    response = _run(provider.search("公开主题", max_results=3))

    assert response.provider_name == "bocha"
    assert response.results[0].title == "标题 B"
    assert response.results[0].snippet == "摘要 B"
