"""声明式数据源、实时预览和发布门测试。"""

import asyncio
import ipaddress
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.models import AgentSession, SourceOnboardingDraft
from app.agent.source_tools import (
    CreateSourceAdapterDraftTool,
    RunSourceNowTool,
    build_source_onboarding_tools,
)
from app.agent.tools import build_tools
from app.signals.declarative import (
    AdapterPreview,
    AdapterSpec,
    DeclarativeRequest,
    DeclarativeSourceAdapter,
    inspect_source_url,
    preview_adapter,
    validate_public_https_url,
)
from app.signals.models import DataSource
from app.signals.schemas import ManualSignalInput
from app.signals.sources import SourceFetchError


def _spec(url: str = "https://official.example/events") -> AdapterSpec:
    return AdapterSpec.model_validate(
        {
            "format": "json",
            "request": {"method": "GET", "url": url},
            "items_path": "data.items",
            "mapping": {
                "external_id": "id",
                "title": "title",
                "content": "description",
                "url": "url",
                "published_at": "published_at",
            },
            "fingerprint_fields": ["external_id", "title", "published_at"],
        }
    )


def test_json_preview_maps_standard_signals() -> None:
    payload = {
        "data": {
            "items": [
                {
                    "id": "event-1",
                    "title": "港口临时关闭",
                    "description": "官方公告内容",
                    "url": "/events/1",
                    "published_at": "2026-08-09T08:00:00+08:00",
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "official.example"
        return httpx.Response(200, content=json.dumps(payload).encode())

    result = asyncio.run(
        preview_adapter(
            "official-events",
            _spec(),
            transport=httpx.MockTransport(handler),
        )
    )
    assert result.fetched_count == 1
    assert result.items[0].external_id == "event-1"
    assert str(result.items[0].url) == "https://official.example/events/1"
    assert result.items[0].published_at is not None


def test_csv_preview_uses_column_names() -> None:
    spec = AdapterSpec.model_validate(
        {
            "format": "csv",
            "request": {"url": "https://official.example/events.csv"},
            "mapping": {
                "external_id": "id",
                "title": "title",
                "content": "content",
            },
        }
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text="id,title,content\n1,官方预警,预警正文\n")
    )
    result = asyncio.run(preview_adapter("official-csv", spec, transport=transport))
    assert result.items[0].title == "官方预警"


def test_html_preview_uses_selectors_and_link_attribute() -> None:
    spec = AdapterSpec.model_validate(
        {
            "format": "html",
            "request": {"url": "https://official.example/notices"},
            "items_selector": "article.notice",
            "mapping": {
                "external_id": "[data-id]@data-id",
                "title": ".title",
                "content": ".summary",
                "url": "a.detail@href",
                "published_at": "time",
            },
        }
    )
    html = """
    <html><head><title>官方公告</title></head><body>
      <article class="notice" data-id="n-1"><h2 class="title">港口关闭</h2>
        <p class="summary">官方公告内容</p><time>2026-08-09T08:00:00+08:00</time>
        <a class="detail" href="/notices/1">详情</a></article>
    </body></html>
    """
    result = asyncio.run(
        preview_adapter(
            "official-html",
            spec,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html)),
        )
    )
    assert result.fetched_count == 1
    assert result.items[0].external_id == "n-1"
    assert str(result.items[0].url) == "https://official.example/notices/1"


def test_html_preview_supports_attribute_operators_and_self_selector() -> None:
    """selector 必须支持 *= ^= $= 等 CSS 操作符 + self keyword（事故通报场景）。"""
    spec = AdapterSpec.model_validate(
        {
            "format": "html",
            "request": {"url": "https://www.mem.gov.cn/xw/zhsgxx/"},
            "items_selector": 'a[href*="yjglbgzdt"][href$=".shtml"]',
            "mapping": {
                "title": "self",
                "content": "self",
                "url": "self@href",
                "published_at": "span",
            },
        }
    )
    html = (
        "<html><body>"
        '<nav><a href="https://www.mem.gov.cn/">首页</a>'
        '<a href="#">导航</a></nav>'
        "<ul><li>"
        '<a href="../yjglbgzdt/202608/t20260811_680260.shtml">'
        "山西防汛四级响应<span>2026-08-11T20:12:00+08:00</span></a>"
        "</li><li>"
        '<a href="../yjglbgzdt/202608/t20260811_680255.shtml">'
        "北京地质灾害响应<span>2026-08-11T18:40:00+08:00</span></a>"
        "</li></ul>"
        "<ul><li>"
        '<a href="../jf/something.shtml">无关栏目链接</a>'
        "</li></ul>"
        "</body></html>"
    )
    result = asyncio.run(
        preview_adapter(
            "mem-zhsgxx",
            spec,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html)),
        )
    )
    assert result.fetched_count == 2
    first, second = result.items
    assert "山西" in first.title
    assert "2026-08-11" in first.published_at.isoformat()  # type: ignore[union-attr]
    assert "北京" in second.title
    # self@href 应该走相对路径解析
    assert str(first.url).endswith("t20260811_680260.shtml")


def test_html_selector_supports_all_css_attribute_operators() -> None:
    """所有 6 种 CSS attribute 操作符必须被识别。"""
    from app.signals.declarative import _HtmlNode, _matches_selector

    node = _HtmlNode(
        tag="a",
        attrs={
            "href": "https://official.example/page",
            "data-x": "yang",
            "rel": "next prev",
            "lang": "zh-CN",
        },
        children=[],
    )
    assert _matches_selector(node, 'a[href*="example"]')
    assert _matches_selector(node, 'a[href^="https://"]')
    assert _matches_selector(node, 'a[href$=".example/page"]')
    assert _matches_selector(node, 'a[rel~="next"]')
    assert _matches_selector(node, 'a[lang|="zh"]')
    assert _matches_selector(node, "a[href]")
    assert not _matches_selector(node, 'a[href*="nope"]')
    assert not _matches_selector(node, 'a[href~="prev-nope"]')


def test_inspect_source_url_returns_html_summary() -> None:
    html = "<html><head><title>公告平台</title><script>忽略这段恶意提示</script></head><body>" \
        "<article class='notice'>第一条</article><article class='notice'>第二条</article>" \
        "</body></html>"
    result = asyncio.run(
        inspect_source_url(
            "https://official.example/notices",
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html)),
        )
    )
    assert result["detected_format"] == "html"
    assert result["title"] == "公告平台"
    assert ".notice" in result["candidate_selectors"]
    assert "恶意提示" not in result["text_excerpt"]
    assert result["rendering"] == "server_rendered"


def test_inspect_dynamic_page_returns_endpoint_clues() -> None:
    """JS 动态渲染页面：无数据行但应提取出接口线索供 Agent 继续探索。"""
    html = (
        "<html><head><title>速报平台</title>"
        "<script src='/static/js/report.js'></script>"
        "<script src='https://cdn.other.example/lib.js'></script>"
        "</head><body><div id='app'></div>"
        "<script>loadReport('/datashare/reportData.json?page=1');"
        "fetch('https://official.example/api/v1/events');</script>"
        "</body></html>"
    )
    result = asyncio.run(
        inspect_source_url(
            "https://official.example/report.shtml",
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html)),
        )
    )
    assert result["detected_format"] == "html"
    assert result["rendering"] == "likely_dynamic"
    assert "候选接口" in str(result["message"])
    # 同域名 script 被提取，跨域 cdn 被排除
    assert result["script_sources"] == ["https://official.example/static/js/report.js"]
    hints = result["endpoint_hints"]
    assert "https://official.example/datashare/reportData.json?page=1" in hints
    assert "https://official.example/api/v1/events" in hints


def test_inspect_dynamic_page_without_clues_falls_back() -> None:
    """既无数据行也无线索时仍按服务端渲染处理（交给选择器适配器验证）。"""
    html = "<html><body><div class='empty'></div></body></html>"
    result = asyncio.run(
        inspect_source_url(
            "https://official.example/blank",
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=html)),
        )
    )
    assert result["rendering"] == "server_rendered"
    assert result["endpoint_hints"] == []


def test_private_network_and_secret_static_headers_are_rejected() -> None:
    with pytest.raises(SourceFetchError, match="非公网"):
        asyncio.run(validate_public_https_url("https://127.0.0.1/events"))
    with pytest.raises(ValidationError, match="静态请求头不得包含凭据"):
        DeclarativeRequest(
            url="https://official.example/events",
            headers={"Authorization": "Bearer must-not-be-stored"},
        )


def test_http_requires_allow_http_hosts_whitelist() -> None:
    """HTTP 明文数据源必须显式加入 allow_http_hosts 白名单。"""
    # 默认拒绝 HTTP
    with pytest.raises(ValidationError, match="allow_http_hosts"):
        DeclarativeRequest(url="http://aqygzj.mofcom.gov.cn/")
    # 白名单放行
    req = DeclarativeRequest(
        url="http://aqygzj.mofcom.gov.cn/",
        allow_http_hosts={"aqygzj.mofcom.gov.cn"},
    )
    assert req.url.startswith("http://")
    # 白名单不匹配仍拒绝
    with pytest.raises(ValidationError, match="allow_http_hosts"):
        DeclarativeRequest(
            url="http://evil.example/",
            allow_http_hosts={"aqygzj.mofcom.gov.cn"},
        )
    # HTTPS 不受影响
    DeclarativeRequest(url="https://www.mem.gov.cn/xw/zhsgxx/")


def test_validator_returns_public_ips_for_pinning(monkeypatch) -> None:
    """成功路径必须返回公网 IP 列表，供 PinnedIPTransport 锁定。"""
    import socket as _socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("1.1.1.1", port)),
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
        ]

    monkeypatch.setattr(
        "app.signals.declarative.socket.getaddrinfo", fake_getaddrinfo
    )
    ips = asyncio.run(
        validate_public_https_url("https://official.example/events")
    )
    assert isinstance(ips, list) and ips
    assert all(ipaddress.ip_address(ip).is_global for ip in ips)


def test_validator_keeps_literal_public_ip() -> None:
    """字面量公网 IP 无需再次解析，直接返回自身。"""
    ips = asyncio.run(
        validate_public_https_url("https://140.82.112.3/repo")
    )
    assert ips == ["140.82.112.3"]


def test_validator_rejects_all_private_dns(monkeypatch) -> None:
    """全部 IP 都是私网（SSRF 攻击场景）必须被拒。"""
    import socket as _socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("192.168.1.1", port)),
        ]

    monkeypatch.setattr(
        "app.signals.declarative.socket.getaddrinfo", fake_getaddrinfo
    )
    with pytest.raises(SourceFetchError, match="非公网"):
        asyncio.run(validate_public_https_url("https://attacker.example/"))


def test_validator_allows_mixed_public_private_dns(monkeypatch) -> None:
    """CDN/负载均衡场景下混合公私网 IP 应放行，并返回公网 IP 列表。"""
    import socket as _socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("1.1.1.1", port)),
            (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(
        "app.signals.declarative.socket.getaddrinfo", fake_getaddrinfo
    )
    ips = asyncio.run(validate_public_https_url("https://cdn.example/"))
    assert ips == ["1.1.1.1"]


def test_validator_rejects_localhost_and_internal_suffix() -> None:
    with pytest.raises(SourceFetchError, match="内部网络"):
        asyncio.run(validate_public_https_url("https://api.internal/"))
    with pytest.raises(SourceFetchError, match="内部网络"):
        asyncio.run(validate_public_https_url("https://service.local/x"))


def test_validator_rejects_http_scheme() -> None:
    with pytest.raises(SourceFetchError, match="HTTPS"):
        asyncio.run(validate_public_https_url("http://official.example/"))


def test_response_size_limit_is_enforced() -> None:
    spec = _spec()
    spec.request.max_response_bytes = 1024
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, content=b"x" * 1025)
    )
    with pytest.raises(SourceFetchError, match="超过 1024"):
        asyncio.run(preview_adapter("oversized", spec, transport=transport))


def test_admin_tools_are_hidden_without_current_message_confirmation() -> None:
    viewer_names = {tool.name for tool in build_tools()}
    admin_names = {
        tool.name
        for tool in build_source_onboarding_tools(
            actor_id="test-admin", allow_publish=False, allow_run=False
        )
    }
    confirmed_names = {
        tool.name
        for tool in build_source_onboarding_tools(
            actor_id="test-admin", allow_publish=True, allow_run=True
        )
    }
    assert viewer_names == {
        "query_suppliers",
        "query_current_alerts",
        "verify_company",
        "get_budget",
    }
    assert viewer_names.isdisjoint(admin_names | confirmed_names)
    assert "preview_source_adapter" not in viewer_names
    assert {
        "inspect_source_url",
        "preview_source_adapter",
        "create_source_adapter_draft",
    } <= admin_names
    assert "publish_source_adapter" not in admin_names
    assert {"publish_source_adapter", "run_source_now"} <= confirmed_names


def test_source_agent_endpoint_requires_permission(client: TestClient, auth_as) -> None:
    auth_as("viewer", "source-agent-viewer")
    denied = client.post(
        "/api/v1/source-agent/chat",
        json={"question": "接入一个数据源", "session_id": None},
    )
    assert denied.status_code == 403

    auth_as("risk_admin", "source-agent-admin")
    allowed = client.post(
        "/api/v1/source-agent/chat",
        json={"question": "接入一个数据源", "session_id": None},
    )
    assert allowed.status_code == 200


def test_source_agent_draft_box_requires_permission(client: TestClient, auth_as) -> None:
    auth_as("viewer", "draft-box-viewer")
    denied = client.get("/api/v1/source-agent/drafts")
    assert denied.status_code == 403

    auth_as("risk_admin", "draft-box-admin")
    allowed = client.get("/api/v1/source-agent/drafts")
    assert allowed.status_code == 200


def test_create_adapter_draft_links_onboarding_draft(db_session) -> None:
    agent_session = AgentSession(agent_kind="source_onboarding")
    db_session.add(agent_session)
    db_session.flush()
    onboarding = SourceOnboardingDraft(
        agent_session_id=agent_session.id,
        actor_id="test-admin",
        current_step="generate_adapter",
        answers={"source_url": "https://official.example/events"},
    )
    db_session.add(onboarding)
    db_session.flush()
    result = asyncio.run(
        CreateSourceAdapterDraftTool("test-admin", onboarding.id).execute(
            {
                "code": "onboarding-linked-draft",
                "name": "关联接入草稿",
                "source_type": "official_api",
                "credibility": 90,
                "schedule": "*/30 * * * *",
                "auth_type": "none",
                "credential_ref": None,
                "login_config": {},
                "description": "测试关联",
                "adapter_config": _spec().model_dump(mode="json"),
            },
            db_session,
        )
    )
    db_session.refresh(onboarding)
    assert result["status"] == "success"
    assert onboarding.source_id == result["source_id"]
    assert onboarding.current_step == "completed"


def test_source_draft_preview_publish_and_enable_api(
    client, db_session, monkeypatch, auth_as
) -> None:
    payload = {
        "code": "declarative-test",
        "name": "声明式测试源",
        "source_type": "official_api",
        "credibility": 90,
        "schedule": "*/30 * * * *",
        "endpoint_url": "https://official.example/events",
        "auth_type": "none",
        "login_config": {},
        "credential_ref": None,
        "description": "测试",
        "adapter_config": _spec().model_dump(mode="json"),
        "enabled": False,
    }
    auth_as("viewer", "declarative-viewer")
    denied = client.post(
        "/api/v1/sources/preview",
        json={
            "source_code": payload["code"],
            "adapter_config": payload["adapter_config"],
        },
    )
    assert denied.status_code == 403

    auth_as("risk_admin", "declarative-risk-admin")
    created = client.post(
        "/api/v1/sources",
        json=payload,
        headers={"X-User-Role": "admin"},
    )
    assert created.status_code == 201
    source_id = created.json()["id"]
    assert source_id > 0
    assert created.json()["adapter_status"] == "draft"
    assert created.json()["enabled"] is False

    draft_box = client.get(
        "/api/v1/source-agent/drafts", headers={"X-User-Role": "admin"}
    )
    assert draft_box.status_code == 200
    assert any(
        item["kind"] == "adapter_draft" and item["source_id"] == source_id
        for item in draft_box.json()["items"]
    )

    blocked = client.put(
        f"/api/v1/sources/{source_id}",
        json={"enabled": True},
        headers={"X-User-Role": "admin"},
    )
    assert blocked.status_code == 409

    async def fake_preview(*args, **kwargs):
        return AdapterPreview(
            fetched_count=1,
            items=[ManualSignalInput(external_id="1", title="测试", content="正文")],
        )

    import app.signals.router as source_router

    monkeypatch.setattr(source_router, "preview_adapter", fake_preview)
    published = client.post(
        f"/api/v1/sources/{source_id}/publish",
        headers={"X-User-Role": "admin", "X-User-Id": "test-admin"},
    )
    assert published.status_code == 200
    assert published.json()["adapter_status"] == "published"
    assert published.json()["adapter_version"] == 1
    assert published.json()["enabled"] is False

    pending_enable = client.get(
        "/api/v1/source-agent/drafts", headers={"X-User-Role": "admin"}
    )
    assert any(
        item["kind"] == "pending_enable" and item["source_id"] == source_id
        for item in pending_enable.json()["items"]
    )

    enabled = client.put(
        f"/api/v1/sources/{source_id}",
        json={"enabled": True},
        headers={"X-User-Role": "admin"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    auth_as("viewer", "declarative-viewer-2")
    denied_disable = client.put(
        f"/api/v1/sources/{source_id}",
        json={"enabled": False},
    )
    assert denied_disable.status_code == 403

    auth_as("risk_admin", "declarative-risk-admin-2")
    disabled = client.put(
        f"/api/v1/sources/{source_id}",
        json={"enabled": False},
        headers={"X-User-Role": "admin"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_run_source_now_rejects_disabled_source(db_session) -> None:
    source = DataSource(
        code="published-disabled",
        name="已发布未启用数据源",
        source_type="official_api",
        credibility=90,
        endpoint_url="https://official.example/events",
        adapter_config=_spec().model_dump(mode="json"),
        adapter_status="published",
        adapter_version=1,
        enabled=False,
    )
    db_session.add(source)
    db_session.flush()
    result = asyncio.run(
        RunSourceNowTool().execute(
            {"source_id": source.id, "confirmation": "立即采集"}, db_session
        )
    )
    assert result["status"] == "error"
    assert "尚未启用" in str(result["message"])


def test_source_agent_delete_draft_flow(client, db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "draft-owner")
    agent_session = AgentSession(
        agent_kind="source_onboarding", owner_user_id=owner.id
    )
    db_session.add(agent_session)
    db_session.flush()
    draft = SourceOnboardingDraft(
        agent_session_id=agent_session.id,
        actor_id="test-admin",
        current_step="collection_goal",
        answers={"source_url": "https://official.example/events"},
    )
    db_session.add(draft)
    db_session.flush()
    draft_id = draft.id

    auth_as("viewer", "draft-viewer")
    denied = client.delete(
        f"/api/v1/source-agent/drafts/{draft_id}",
        headers={"X-User-Role": "admin"},
    )
    assert denied.status_code == 403

    owner_2 = auth_as("risk_admin", "draft-owner-2")
    agent_session.owner_user_id = owner_2.id
    db_session.commit()
    box = client.get("/api/v1/source-agent/drafts", headers={"X-User-Role": "admin"})
    assert box.status_code == 200
    assert any(
        item["kind"] == "in_progress" and item["draft_id"] == draft_id
        for item in box.json()["items"]
    )

    deleted = client.delete(
        f"/api/v1/source-agent/drafts/{draft_id}", headers={"X-User-Role": "admin"}
    )
    assert deleted.status_code == 204
    assert db_session.get(SourceOnboardingDraft, draft_id) is None

    missing = client.delete(
        f"/api/v1/source-agent/drafts/{draft_id}", headers={"X-User-Role": "admin"}
    )
    assert missing.status_code == 404


def test_source_agent_delete_completed_draft_rejected(client, db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "completed-draft-owner")
    source = DataSource(
        code="linked-source",
        name="已关联数据源的草稿",
        source_type="official_api",
        credibility=90,
        endpoint_url="https://official.example/events",
        adapter_config=_spec().model_dump(mode="json"),
        adapter_status="draft",
        enabled=False,
    )
    db_session.add(source)
    db_session.flush()
    agent_session = AgentSession(
        agent_kind="source_onboarding", owner_user_id=owner.id
    )
    db_session.add(agent_session)
    db_session.flush()
    draft = SourceOnboardingDraft(
        agent_session_id=agent_session.id,
        actor_id="test-admin",
        current_step="completed",
        answers={"source_url": "https://official.example/events"},
        source_id=source.id,
    )
    db_session.add(draft)
    db_session.flush()

    response = client.delete(
        f"/api/v1/source-agent/drafts/{draft.id}", headers={"X-User-Role": "admin"}
    )
    assert response.status_code == 409
    assert db_session.get(SourceOnboardingDraft, draft.id) is not None


# === Crawl4AI 回退集成测试（S2 验收）===========================


def _mem_spec(*, use_fallback: bool) -> AdapterSpec:
    return AdapterSpec.model_validate(
        {
            "format": "html",
            "request": {"url": "https://www.mem.gov.cn/xw/zhsgxx/"},
            "items_selector": "li",
            "mapping": {
                "title": "a",
                "content": "a",
                "url": "a@href",
                "published_at": "span",
            },
            "use_crawl4ai_fallback": use_fallback,
        }
    )


def test_fallback_triggers_on_403_when_enabled() -> None:
    """403 + use_crawl4ai_fallback=True 时必须转 Crawl4AI 路径。

    _fetch_with_fallback 内部检测到 Crawl4AI 未配置（token 空）会抛
    SourceRequestFailed(error_kind=crawler_unavailable)，
    由 adapter 包成 SourceFetchError(error_kind=crawler_unavailable) 抛出。
    本测试断言：触发后错误分类仍是 crawler_unavailable（不被误判为 access_blocked）。
    """
    monkey = pytest.MonkeyPatch()
    # 显式开启监控轨 Crawl4AI，但 token 留空，让 fallback 在初始化阶段就抛
    # crawler_unavailable，便于测试断言。
    monkey.setenv("MONITOR_CRAWL4AI_ENABLED", "true")
    monkey.setenv("MONITOR_CRAWL4AI_API_TOKEN", "")
    monkey.setenv("MONITOR_CRAWL4AI_BASE_URL", "http://crawl4ai.test:11235")

    def primary_handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html>blocked</html>",
            headers={"Content-Type": "text/html"},
        )

    try:
        adapter = DeclarativeSourceAdapter(
            "mem-test",
            _mem_spec(use_fallback=True),
            transport=_transport(primary_handler),
        )
        with pytest.raises(SourceFetchError) as exc_info:
            asyncio.run(adapter.fetch())
        assert exc_info.value.error_kind == "crawler_unavailable"
    finally:
        monkey.undo()


def test_no_fallback_when_disabled() -> None:
    """use_crawl4ai_fallback=False 时 403 直接抛出原错误，不调 fallback。"""

    def primary_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html>blocked</html>",
            headers={"Content-Type": "text/html"},
        )

    adapter = DeclarativeSourceAdapter(
        "mem-test", _mem_spec(use_fallback=False), transport=_transport(primary_handler)
    )
    with pytest.raises(SourceFetchError) as exc_info:
        asyncio.run(adapter.fetch())
    assert exc_info.value.error_kind == "access_blocked"


def test_no_fallback_on_404() -> None:
    """404（http_error）不回退，避免 Crawl4AI 重抓无意义 URL。"""

    def primary_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    adapter = DeclarativeSourceAdapter(
        "mem-test", _mem_spec(use_fallback=True), transport=_transport(primary_handler)
    )
    with pytest.raises(SourceFetchError) as exc_info:
        asyncio.run(adapter.fetch())
    assert exc_info.value.http_status == 404


def test_fallback_classifies_rate_limited() -> None:
    """429 也走 fallback；原 error_kind=rate_limited 路径覆盖。

    同上：fallback 内部因 token 空抛 crawler_unavailable，最终错误归类一致。
    """
    monkey = pytest.MonkeyPatch()
    monkey.setenv("MONITOR_CRAWL4AI_ENABLED", "true")
    monkey.setenv("MONITOR_CRAWL4AI_API_TOKEN", "")
    monkey.setenv("MONITOR_CRAWL4AI_BASE_URL", "http://crawl4ai.test:11235")

    def primary_handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited", headers={"Retry-After": "30"})

    try:
        adapter = DeclarativeSourceAdapter(
            "mem-test",
            _mem_spec(use_fallback=True),
            transport=_transport(primary_handler),
        )
        with pytest.raises(SourceFetchError) as exc_info:
            asyncio.run(adapter.fetch())
        assert exc_info.value.error_kind == "crawler_unavailable"
    finally:
        monkey.undo()


def _transport(handler):
    return httpx.MockTransport(handler)
