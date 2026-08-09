"""Agent 模块测试：FakeAgentLLM 全链路，不访问真实模型接口。"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent import budget as budget_module
from app.agent.budget import get_tyc_usage, record_tyc_usage
from app.agent.engine import AgentError, FakeAgentLLM, LLMResponse, ToolCallSpec, run_agent
from app.agent.models import AgentMessage, AgentSession, TycUsageRecord
from app.agent.service import (
    RISK_QUERY,
    SOURCE_ONBOARDING,
    chat,
    chat_source_onboarding,
)
from app.agent.tools import (
    GetBudgetTool,
    QueryCurrentAlertsTool,
    QuerySuppliersTool,
    VerifyCompanyTool,
    build_tools,
)
from app.agent.tyc_gateway import (
    McpTycGateway,
    TycGatewayError,
    UnconfiguredTycGateway,
    build_tyc_gateway,
)
from app.signals.models import DataSource
from app.suppliers.models import Supplier


@pytest.fixture
def clean_agent_tables(db_session: Session) -> Session:
    db_session.execute(delete(AgentMessage))
    db_session.execute(delete(AgentSession))
    db_session.execute(delete(TycUsageRecord))
    db_session.flush()
    return db_session


class FakeTycGateway:
    """测试用网关：status 可配置，模拟天眼查返回。"""

    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.calls: list[str] = []

    async def verify(self, company_name: str) -> dict[str, object]:
        self.calls.append(company_name)
        if self.status == "success":
            return {
                "status": "success",
                "company_name": company_name,
                "reg_status": "存续",
                "lawsuits": 0,
            }
        if self.status == "empty":
            return {"status": "empty", "message": "无该企业相关记录"}
        if self.status == "error":
            raise RuntimeError("天眼查服务暂时不可用")
        return {"status": "error", "message": "未知错误"}


@pytest.fixture
def enable_tyc(db_session: Session, monkeypatch: MonkeyPatch) -> None:
    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    if source is None:
        source = DataSource(
            code="tianyancha",
            name="天眼查企业核查",
            source_type="external_tool",
            credibility=90,
            schedule=None,
            endpoint_url="https://mcp.tianyancha.com/v1",
            auth_type="api_key",
            login_config={},
            credential_ref="env:TYC_API_KEY",
            description="测试数据源",
            enabled=True,
        )
        db_session.add(source)
    else:
        source.enabled = True
    db_session.flush()
    monkeypatch.setattr(budget_module.config, "TYC_API_KEY", "tyc_test_key")
    monkeypatch.setattr(budget_module, "AGENT_TYC_DAILY_LIMIT", 5)
    monkeypatch.setattr(budget_module, "AGENT_TYC_MONTHLY_LIMIT", 50)


def test_chat_creates_session_and_persists_messages(
    clean_agent_tables: Session,
) -> None:
    response = asyncio.run(
        chat(clean_agent_tables, "今天有什么风险？", llm=FakeAgentLLM())
    )
    assert response.session_id > 0
    assert "Fake" in response.answer
    assert [call.name for call in response.tool_calls] == ["query_current_alerts"]

    messages = list(
        clean_agent_tables.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == response.session_id)
            .order_by(AgentMessage.id)
        )
    )
    assert [message.role for message in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant.tool_calls[0]["name"] == "query_current_alerts"
    assert assistant.tool_calls[0]["result"]["total"] == 0
    assert clean_agent_tables.get(AgentSession, response.session_id).agent_kind == RISK_QUERY


def test_chat_continues_existing_session(clean_agent_tables: Session) -> None:
    first = asyncio.run(chat(clean_agent_tables, "今天有什么风险？", llm=FakeAgentLLM()))
    second = asyncio.run(
        chat(
            clean_agent_tables,
            "再帮我查一下供应商",
            session_id=first.session_id,
            llm=FakeAgentLLM(),
        )
    )
    assert second.session_id == first.session_id
    count = len(
        clean_agent_tables.scalars(
            select(AgentMessage).where(AgentMessage.session_id == first.session_id)
        ).all()
    )
    assert count == 4


def test_chat_without_risk_keyword_calls_no_tool(
    clean_agent_tables: Session,
) -> None:
    response = asyncio.run(chat(clean_agent_tables, "你好", llm=FakeAgentLLM()))
    assert response.tool_calls == []


def test_two_agents_use_disjoint_tools_and_sessions(
    clean_agent_tables: Session,
) -> None:
    class CapturingLLM(FakeAgentLLM):
        def __init__(self) -> None:
            self.tool_names: set[str] = set()

        async def respond(
            self,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
        ) -> LLMResponse:
            del messages
            self.tool_names = {
                str(tool["function"]["name"])
                for tool in tools
                if isinstance(tool.get("function"), dict)
            }
            return LLMResponse(content="隔离测试完成")

    risk_llm = CapturingLLM()
    risk = asyncio.run(
        chat(clean_agent_tables, "接入数据源并确认发布立即采集", llm=risk_llm)
    )
    source_llm = CapturingLLM()
    source = asyncio.run(
        chat_source_onboarding(
            clean_agent_tables,
            "查询风险并确认发布立即采集",
            llm=source_llm,
            actor_id="test-admin",
        )
    )

    assert risk_llm.tool_names == {
        "query_suppliers",
        "query_current_alerts",
        "verify_company",
        "get_budget",
    }
    assert source_llm.tool_names == {
        "inspect_source_url",
        "preview_source_adapter",
        "create_source_adapter_draft",
        "publish_source_adapter",
        "run_source_now",
    }
    assert risk_llm.tool_names.isdisjoint(source_llm.tool_names)
    assert clean_agent_tables.get(AgentSession, risk.session_id).agent_kind == RISK_QUERY
    assert (
        clean_agent_tables.get(AgentSession, source.session_id).agent_kind
        == SOURCE_ONBOARDING
    )

    with pytest.raises(AgentError, match="不能跨类型复用"):
        asyncio.run(
            chat_source_onboarding(
                clean_agent_tables,
                "继续",
                session_id=risk.session_id,
                llm=source_llm,
            )
        )
    with pytest.raises(AgentError, match="不能跨类型复用"):
        asyncio.run(
            chat(
                clean_agent_tables,
                "继续",
                session_id=source.session_id,
                llm=risk_llm,
            )
        )


def test_run_agent_max_steps_guard(clean_agent_tables: Session) -> None:
    class LoopingLLM(FakeAgentLLM):
        async def respond(
            self,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
        ) -> LLMResponse:
            return LLMResponse(tool_calls=[ToolCallSpec("get_budget", {})])

    with pytest.raises(AgentError, match="最大步数"):
        asyncio.run(
            run_agent(
                clean_agent_tables,
                "今天有什么风险？",
                [],
                llm=LoopingLLM(),
                tools=build_tools(),
                max_steps=2,
            )
        )


def test_query_suppliers_tool(clean_agent_tables: Session) -> None:
    clean_agent_tables.add(
        Supplier(
            supplier_code="SUP-0001",
            legal_name="测试供应商有限公司",
            country_code="CN",
            registry_no="91310000TEST00001",
        )
    )
    clean_agent_tables.flush()
    result = asyncio.run(QuerySuppliersTool().execute({"keyword": "测试"}, clean_agent_tables))
    assert result["total"] == 1
    assert result["items"][0]["legal_name"] == "测试供应商有限公司"  # type: ignore[index]


def test_query_alerts_tool_returns_empty(clean_agent_tables: Session) -> None:
    result = asyncio.run(QueryCurrentAlertsTool().execute({}, clean_agent_tables))
    assert result["total"] == 0


def test_verify_company_defaults_to_not_configured(
    clean_agent_tables: Session,
) -> None:
    result = asyncio.run(
        VerifyCompanyTool().execute({"company_name": "某科技有限公司"}, clean_agent_tables)
    )
    assert result["status"] == "not_configured"


def test_verify_company_charges_on_success(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    gateway = FakeTycGateway(status="success")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "某科技有限公司"}, clean_agent_tables)
    )
    assert result["status"] == "success"
    assert result["usage"]["daily_used"] == 1  # type: ignore[index]
    assert gateway.calls == ["某科技有限公司"]
    usage = get_tyc_usage(clean_agent_tables)
    assert usage.daily_used == 1


def test_verify_company_quota_exhausted_blocks_call(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    # 先塞满当日额度
    for i in range(5):
        record_tyc_usage(
            clean_agent_tables, tool_name="verify_company", company_name=f"C{i}", status="success"
        )
    clean_agent_tables.flush()
    gateway = FakeTycGateway(status="success")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "新公司"}, clean_agent_tables)
    )
    assert result["status"] == "quota_exhausted"
    assert gateway.calls == []  # 网关未被调用


def test_verify_company_error_does_not_charge(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    gateway = FakeTycGateway(status="error")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "某公司"}, clean_agent_tables)
    )
    # 网关异常被引擎兜底为 error，不计费
    assert result["status"] == "error"
    assert get_tyc_usage(clean_agent_tables).daily_used == 0


def test_verify_company_empty_does_not_charge(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    gateway = FakeTycGateway(status="empty")
    tool = VerifyCompanyTool(gateway=gateway)
    result = asyncio.run(
        tool.execute({"company_name": "某公司"}, clean_agent_tables)
    )
    assert result["status"] == "empty"
    assert get_tyc_usage(clean_agent_tables).daily_used == 0


def test_get_budget_returns_real_counts(
    clean_agent_tables: Session, enable_tyc: None
) -> None:
    record_tyc_usage(
        clean_agent_tables, tool_name="verify_company", company_name="C1", status="success"
    )
    clean_agent_tables.flush()
    result = asyncio.run(GetBudgetTool().execute({}, clean_agent_tables))
    assert result["daily_used"] == 1
    assert result["daily_remaining"] == 4  # type: ignore[index]
    assert result["monthly_remaining"] == 49  # type: ignore[index]


CANDIDATES_MD = (
    "| # | 企业名称 | 统一社会信用代码 | 登记状态 | 法定代表人 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| 1 | 测试科技有限公司 | 91310000TEST00001 | 存续 | 张三 |\n"
    "| 2 | 测试科技有限公司(上海) | 91310000TEST00002 | 注销 | 李四 |\n"
)


def _sse_response(body: dict[str, object]) -> httpx.Response:
    data = json.dumps(body, ensure_ascii=False)
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        text=f"event: message\ndata: {data}\n\n",
    )


def _mcp_handler() -> tuple[object, list[str]]:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization", ""))
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "tyc-mcp", "version": "2.2.0"},
                    },
                }
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            name = payload["params"]["name"]  # type: ignore[index]
            assert name == "search_companies"
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": CANDIDATES_MD}],
                        "isError": False,
                    },
                }
            )
        return httpx.Response(400)

    return handler, captured


def test_mcp_gateway_sse_success() -> None:
    handler, captured = _mcp_handler()
    gateway = McpTycGateway("tyc_test_key", transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.verify("测试科技有限公司"))
    assert result["status"] == "success"
    assert result["company_name"] == "测试科技有限公司"
    assert result["credit_code"] == "91310000TEST00001"
    assert result["reg_status"] == "存续"
    assert all(header == "tyc_test_key" for header in captured)


def test_mcp_gateway_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": CANDIDATES_MD}],
                        "isError": False,
                    },
                },
            )
        return httpx.Response(202)

    gateway = McpTycGateway("tyc_test_key", transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.verify("测试科技有限公司"))
    assert result["status"] == "success"


def test_mcp_gateway_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return _sse_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
            )
        if payload.get("method") == "tools/call":
            return _sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "未查询到相关记录"}],
                        "isError": False,
                    },
                }
            )
        return httpx.Response(202)

    gateway = McpTycGateway("tyc_test_key", transport=httpx.MockTransport(handler))
    result = asyncio.run(gateway.verify("不存在的公司"))
    assert result["status"] == "empty"


def test_mcp_gateway_auth_failure_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return _sse_response(
                {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
            )
        return httpx.Response(401)

    gateway = McpTycGateway("bad_key", transport=httpx.MockTransport(handler))
    with pytest.raises(TycGatewayError, match="鉴权失败"):
        asyncio.run(gateway.verify("测试公司"))


def test_build_tyc_gateway_defaults_to_unconfigured(
    monkeypatch: MonkeyPatch,
) -> None:
    import app.agent.tyc_gateway as gateway_module

    monkeypatch.setattr(gateway_module, "TYC_API_KEY", "", raising=False)
    import app.config as config_module

    monkeypatch.setattr(config_module, "TYC_API_KEY", "")
    gateway = build_tyc_gateway()
    assert isinstance(gateway, UnconfiguredTycGateway)
    result = asyncio.run(gateway.verify("测试公司"))
    assert result["status"] == "not_configured"


def test_chat_endpoint(
    client: TestClient, clean_agent_tables: Session, monkeypatch: MonkeyPatch
) -> None:
    import app.agent.service as agent_service

    # 固定使用 Fake 引擎，避免依赖真实模型网络（测试环境无关）
    monkeypatch.setattr(agent_service, "get_agent_llm", lambda: FakeAgentLLM())
    response = client.post("/api/v1/chat", json={"question": "今天有什么风险？"})
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] > 0
    assert body["answer"]
    assert body["tool_calls"][0]["name"] == "query_current_alerts"


def test_chat_endpoint_validation(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"question": ""})
    assert response.status_code == 422


def test_agent_endpoints_have_separate_openapi_groups(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]
    assert paths["/api/v1/chat"]["post"]["tags"] == ["风险查询助手"]
    assert paths["/api/v1/source-agent/chat"]["post"]["tags"] == ["数据源接入助手"]


def test_agent_status_endpoint(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    import app.agent.router as agent_router
    from app.config import AISettings

    monkeypatch.setattr(
        agent_router,
        "get_ai_settings",
        lambda: AISettings(
            provider="fake",
            base_url="",
            model="",
            api_key="",
            timeout_seconds=30,
            max_retries=2,
        ),
    )
    monkeypatch.setattr(budget_module.config, "TYC_API_KEY", "")
    response = client.get("/api/v1/agent/status")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_configured"] is False  # Fake 引擎视为未配置真实模型
    assert body["tyc_enabled"] is False
    assert body["max_steps"] >= 1
