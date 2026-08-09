"""声明式数据源、实时预览和发布门测试。"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.source_tools import build_source_onboarding_tools
from app.agent.tools import build_tools
from app.signals.declarative import (
    AdapterPreview,
    AdapterSpec,
    DeclarativeRequest,
    inspect_source_url,
    preview_adapter,
    validate_public_https_url,
)
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


def test_private_network_and_secret_static_headers_are_rejected() -> None:
    with pytest.raises(SourceFetchError, match="非公网"):
        asyncio.run(validate_public_https_url("https://127.0.0.1/events"))
    with pytest.raises(ValidationError, match="静态请求头不得包含凭据"):
        DeclarativeRequest(
            url="https://official.example/events",
            headers={"Authorization": "Bearer must-not-be-stored"},
        )


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


def test_source_agent_endpoint_requires_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/source-agent/chat",
        json={"question": "接入一个数据源", "session_id": None},
    )
    assert response.status_code == 403


def test_source_draft_preview_publish_and_enable_api(client, db_session, monkeypatch) -> None:
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
    denied = client.post(
        "/api/v1/sources/preview",
        json={
            "source_code": payload["code"],
            "adapter_config": payload["adapter_config"],
        },
    )
    assert denied.status_code == 403

    created = client.post(
        "/api/v1/sources",
        json=payload,
        headers={"X-User-Role": "admin"},
    )
    assert created.status_code == 201
    source_id = created.json()["id"]
    assert created.json()["adapter_status"] == "draft"
    assert created.json()["enabled"] is False

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

    enabled = client.put(
        f"/api/v1/sources/{source_id}",
        json={"enabled": True},
        headers={"X-User-Role": "admin"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
