"""MofcomEntityDetailAdapter（商务部实体名单详情解析）单测。"""

from __future__ import annotations

import asyncio

import pytest
from mofcom_detail_support import (
    DETAIL_MARKDOWN,
    ENTITY_TITLES,
    HOMEPAGE_URL,
    NO_ENTITY_DETAIL_MARKDOWN,
    NON_ENTITY_MARKDOWN,
    NON_QUALIFYING_DETAIL_URL,
    NON_QUALIFYING_HTML,
    NON_QUALIFYING_MARKDOWN,
    NON_QUALIFYING_TITLE,
    QUALIFYING_DETAIL_URL,
    QUALIFYING_TITLE,
    RECOVERABLE_HOMEPAGE_ERRORS,
    SECOND_QUALIFYING_DETAIL_URL,
    TWO_CANDIDATE_HTML,
    controlled_env,
    controlled_failure_env,
    detail_adapter,
    html_anchor,
    html_homepage,
    is_homepage,
    no_entity_details,
)
from mofcom_detail_transport_support import (
    connect_error_transport,
    status_transport,
    transport_env,
)

from app.signals.request_control import SourceRequestFailed
from app.signals.sources import (
    MofcomEntityDetailAdapter,
    SourceFetchError,
    _extract_entities_from_detail,
    _parse_announcement_links_html,
    _parse_announcement_links_markdown,
)


def test_extract_entities_from_detail() -> None:
    entities = _extract_entities_from_detail(DETAIL_MARKDOWN)
    assert "拉法特集团" in entities
    assert "太脱拉卡车公司" in entities
    assert "Opticoelectron集团" in entities
    # 不应包含导航/正文干扰
    assert all("审批" not in entity for entity in entities)


def test_fetch_parses_entities_through_homepage_then_detail(monkeypatch) -> None:
    """真实首页→详情流程：transport 供首页 HTML，详情经 Crawl4AI 桩解析。"""
    adapter = detail_adapter(monkeypatch)
    items = asyncio.run(adapter.fetch())

    assert [item.title for item in items] == ENTITY_TITLES
    first = items[0]
    assert "商务部公告2026年第30号" in first.content or "原文" in first.content
    assert first.external_id.startswith("mofcom-entity-")


def test_normalize_and_fingerprint_roundtrip(monkeypatch) -> None:
    adapter = detail_adapter(monkeypatch)
    item = asyncio.run(adapter.fetch())[0]
    signal = adapter.normalize(item)
    assert signal.title == item.title
    assert adapter.fingerprint(signal) == adapter.fingerprint(signal)
    # 同一来源详情同一实体跨轮次去重语义稳定（external_id 不变）
    assert asyncio.run(adapter.fetch())[0].external_id == item.external_id


def test_healthcheck_ok(monkeypatch) -> None:
    adapter = detail_adapter(monkeypatch)
    health = asyncio.run(adapter.healthcheck())
    assert health.ok is True
    assert health.message == "返回 3 条实体信号"


def test_extract_skips_non_entity_lines() -> None:
    entities = _extract_entities_from_detail(NON_ENTITY_MARKDOWN)
    assert entities == ["拉法特集团", "太脱拉卡车公司"]


def test_fetch_direct_homepage_html_requests_once_and_filters_qualifying(monkeypatch) -> None:
    """直连 HTML 首页只请求一次，只产出合格公告中的实体。"""
    env = transport_env(monkeypatch, guard=True)

    items = asyncio.run(env.adapter.fetch())

    assert len([url for url in env.direct_calls if is_homepage(url)]) == 1
    assert env.crawl_calls.count(HOMEPAGE_URL) == 0
    assert env.source_calls == []
    assert [item.title for item in items] == ENTITY_TITLES
    assert all(item.url != NON_QUALIFYING_DETAIL_URL for item in items)


def test_fetch_falls_back_to_crawl4ai_markdown_when_homepage_502(monkeypatch) -> None:
    """首页直连 502 时，改用 Crawl4AI 返回的 Markdown 链接继续抓详情。"""
    env = transport_env(monkeypatch, status_code=502, homepage_body="", guard=True)

    items = asyncio.run(env.adapter.fetch())

    assert [url for url in env.direct_calls if is_homepage(url)]
    assert env.crawl_calls.count(HOMEPAGE_URL) == 1
    assert env.source_calls == []
    assert [item.title for item in items] == ENTITY_TITLES


def test_fetch_never_reads_declarative_source_config_or_instantiates_adapter(monkeypatch) -> None:
    """生产路径自行抓首页，不得读取 source 4 声明式配置或实例化其适配器。"""
    env = controlled_env(monkeypatch, guard=True, async_client_fallback=True)

    items = asyncio.run(env.adapter.fetch())

    assert len([url for url in env.direct_calls if is_homepage(url)]) == 1
    assert env.source_calls == []
    assert [item.title for item in items] == ENTITY_TITLES
    assert all(item.url != NON_QUALIFYING_DETAIL_URL for item in items)


@pytest.mark.parametrize(("error_kind", "status_code"), RECOVERABLE_HOMEPAGE_ERRORS)
def test_fetch_falls_back_on_recoverable_homepage_error(
    monkeypatch, error_kind, status_code
) -> None:
    """直连首页可恢复失败（403/429/5xx/网络）都回退 Crawl4AI 一次。"""
    env = controlled_failure_env(
        monkeypatch, error_kind=error_kind, status_code=status_code
    )

    items = asyncio.run(env.adapter.fetch())

    assert len(env.direct_calls) == 1
    assert env.crawl_calls.count(HOMEPAGE_URL) == 1
    assert [item.title for item in items] == ENTITY_TITLES


def test_fetch_falls_back_on_homepage_network_exception(monkeypatch) -> None:
    """直连首页抛 httpx 网络异常同样回退，不真实联网。"""
    env = transport_env(monkeypatch, transport=connect_error_transport())

    items = asyncio.run(env.adapter.fetch())

    assert env.crawl_calls.count(HOMEPAGE_URL) == 1
    assert [item.title for item in items] == ENTITY_TITLES


def test_parse_announcement_links_html_supports_quotes_nested_tags_and_entities() -> None:
    """单引号 href、嵌套标签与实体编码文本都按可见文本解析。"""
    body = html_homepage(
        html_anchor(
            QUALIFYING_DETAIL_URL,
            "商务部公告2026年第30号<span>公布将14家</span>"
            "<b>欧盟实体</b>列入出口管制&amp;管控名单",
            quote="'",
        ),
        html_anchor(NON_QUALIFYING_DETAIL_URL, NON_QUALIFYING_TITLE),
    )

    links = _parse_announcement_links_html(body, base_url=HOMEPAGE_URL)

    expected_title = "商务部公告2026年第30号公布将14家欧盟实体列入出口管制&管控名单"
    assert links[0] == (expected_title, QUALIFYING_DETAIL_URL)
    assert links[1] == (NON_QUALIFYING_TITLE, NON_QUALIFYING_DETAIL_URL)


def test_parse_announcement_links_markdown_supports_optional_title() -> None:
    """真实首页 Markdown 链接带可选标题：](url "title") 与 ](url) 都要解析。

    现场证据：http://aqygzj.mofcom.gov.cn/ 的 Crawl4AI Markdown 输出中，
    公告链接尾部带可选引号标题（``](url "title")`` 形态）；旧正则要求 URL
    后紧跟 ``)``，因而整条链接漏解析、回退时报“未解析到合格公告链接”。
    """
    relative_url = (
        "/flzc/gzjgfxwj/art/2026/art_cddc4316cde145edb878144b2772719d.html"
    )
    plain_relative_url = "/flzc/gzjgfxwj/art/2026/art_cddc4316cde145edb878144b2772719e.html"
    body = (
        f'[{QUALIFYING_TITLE}]({relative_url} "{QUALIFYING_TITLE}")\n'
        f"* [{NON_QUALIFYING_TITLE}]({plain_relative_url})\n"
    )

    links = _parse_announcement_links_markdown(body, base_url=HOMEPAGE_URL)

    assert links == [
        (
            QUALIFYING_TITLE,
            "http://aqygzj.mofcom.gov.cn/flzc/gzjgfxwj/art/2026/"
            "art_cddc4316cde145edb878144b2772719d.html",
        ),
        (
            NON_QUALIFYING_TITLE,
            "http://aqygzj.mofcom.gov.cn/flzc/gzjgfxwj/art/2026/"
            "art_cddc4316cde145edb878144b2772719e.html",
        ),
    ]


@pytest.mark.parametrize("homepage_body", ["", NON_QUALIFYING_HTML])
def test_fetch_falls_back_once_when_homepage_has_no_qualifying_link(
    monkeypatch, homepage_body
) -> None:
    """直连 200 但零合格链接：回退一次 Crawl4AI Markdown 后继续采集。"""
    env = controlled_env(monkeypatch, homepage_body=homepage_body)

    items = asyncio.run(env.adapter.fetch())

    assert env.crawl_calls.count(HOMEPAGE_URL) == 1
    assert [item.title for item in items] == ENTITY_TITLES


@pytest.mark.parametrize("homepage_markdown", ["", NON_QUALIFYING_MARKDOWN])
def test_fetch_raises_when_fallback_still_has_no_qualifying_link(
    monkeypatch, homepage_markdown
) -> None:
    """回退仍零合格链接：抛 SourceFetchError，不得成功空采集。"""
    env = controlled_env(
        monkeypatch,
        homepage_body=NON_QUALIFYING_HTML,
        homepage_markdown=homepage_markdown,
    )

    with pytest.raises(SourceFetchError) as excinfo:
        asyncio.run(env.adapter.fetch())

    assert excinfo.value.error_kind == "empty_result"
    assert env.crawl_calls.count(HOMEPAGE_URL) == 1


def test_transport_homepage_zero_links_falls_back_via_crawl4ai(monkeypatch) -> None:
    """transport 首页 200 但零链接：与生产一致走 Crawl4AI 回退，不把首页当详情。"""
    env = transport_env(monkeypatch, homepage_body=html_homepage())

    items = asyncio.run(env.adapter.fetch())

    assert env.crawl_calls.count(HOMEPAGE_URL) == 1
    assert [item.title for item in items] == ENTITY_TITLES


@pytest.mark.parametrize("status_code", [403, 429])
def test_transport_homepage_403_429_triggers_crawl4ai_fallback(
    monkeypatch, status_code
) -> None:
    """显式 transport 的 403/429 与生产一致视为可恢复，回退 Crawl4AI 一次。"""
    env = transport_env(monkeypatch, status_code=status_code)

    items = asyncio.run(env.adapter.fetch())

    assert env.crawl_calls.count(HOMEPAGE_URL) == 1
    assert [item.title for item in items] == ENTITY_TITLES


@pytest.mark.parametrize(
    ("status_code", "expected_kind"),
    [(403, "access_blocked"), (429, "rate_limited"), (503, "upstream_error")],
)
def test_transport_homepage_errors_classified_like_controlled_get(
    status_code, expected_kind
) -> None:
    """显式 transport 的 403/429/5xx 错误分类与生产 controlled_get 一致。"""
    adapter = MofcomEntityDetailAdapter(transport=status_transport(status_code))

    with pytest.raises(SourceFetchError) as excinfo:
        asyncio.run(adapter._fetch_homepage_body())

    assert excinfo.value.error_kind == expected_kind
    assert excinfo.value.http_status == status_code


def test_fetch_raises_empty_result_when_all_details_have_no_entities(monkeypatch) -> None:
    """有合格候选且所有详情成功但零实体：抛 empty_result，不得成功空采集。"""
    env = controlled_env(
        monkeypatch, homepage_body=TWO_CANDIDATE_HTML, detail_markdowns=no_entity_details()
    )

    with pytest.raises(SourceFetchError) as excinfo:
        asyncio.run(env.adapter.fetch())

    assert excinfo.value.error_kind == "empty_result"
    assert env.crawl_calls.count(HOMEPAGE_URL) == 0


def test_fetch_succeeds_when_one_detail_has_entities(monkeypatch) -> None:
    """部分详情为空但至少一个详情产出实体：允许成功，空详情不阻断。"""
    env = controlled_env(
        monkeypatch,
        homepage_body=TWO_CANDIDATE_HTML,
        detail_markdowns={QUALIFYING_DETAIL_URL: NO_ENTITY_DETAIL_MARKDOWN},
    )

    items = asyncio.run(env.adapter.fetch())

    assert [item.title for item in items] == ENTITY_TITLES
    assert all(item.url == SECOND_QUALIFYING_DETAIL_URL for item in items)


def test_fetch_raises_with_first_detail_failure_when_all_details_fail(monkeypatch) -> None:
    """有候选详情但全部抓取失败：抛保留首个 error_kind/http_status 的 SourceFetchError。"""
    env = controlled_env(
        monkeypatch,
        homepage_body=TWO_CANDIDATE_HTML,
        detail_markdowns={
            QUALIFYING_DETAIL_URL: SourceRequestFailed(
                "源站返回 HTTP 503", error_kind="upstream_error", status_code=503
            ),
            SECOND_QUALIFYING_DETAIL_URL: SourceRequestFailed(
                "源站请求限流", error_kind="rate_limited", status_code=429
            ),
        },
    )

    with pytest.raises(SourceFetchError) as excinfo:
        asyncio.run(env.adapter.fetch())

    assert excinfo.value.error_kind == "upstream_error"
    assert excinfo.value.http_status == 503


def test_fetch_keeps_partial_items_when_some_details_fail(monkeypatch) -> None:
    """部分详情失败、其余成功：返回成功详情解析出的实体，不抛错。"""
    env = controlled_env(
        monkeypatch,
        homepage_body=TWO_CANDIDATE_HTML,
        detail_markdowns={
            QUALIFYING_DETAIL_URL: SourceRequestFailed(
                "源站返回 HTTP 502", error_kind="upstream_error", status_code=502
            ),
        },
    )

    items = asyncio.run(env.adapter.fetch())

    assert [item.title for item in items] == ENTITY_TITLES
    assert all(item.url == SECOND_QUALIFYING_DETAIL_URL for item in items)


def test_fetch_caps_detail_pages_at_eight(monkeypatch) -> None:
    """详情候选超过 8 条时只抓前 8 条。"""
    detail_urls = [
        f"http://aqygzj.mofcom.gov.cn/art/2026/07/24/art_{index}.html"
        for index in range(10)
    ]
    env = controlled_env(
        monkeypatch,
        homepage_body=html_homepage(
            *(html_anchor(url, QUALIFYING_TITLE) for url in detail_urls)
        ),
    )

    items = asyncio.run(env.adapter.fetch())

    assert [url for url in env.crawl_calls if not is_homepage(url)] == detail_urls[:8]
    assert len(items) == 24


def test_fetch_caps_entities_at_two_hundred(monkeypatch) -> None:
    """单个详情超过 200 条实体时截断到 200。"""
    big_detail = "\n".join(f"{index}.测试实体{index:03d}" for index in range(1, 251))
    env = controlled_env(monkeypatch, detail_markdowns={QUALIFYING_DETAIL_URL: big_detail})

    items = asyncio.run(env.adapter.fetch())

    assert len(items) == 200
