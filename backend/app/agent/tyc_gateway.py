"""天眼查 MCP 网关（Streamable HTTP / JSON-RPC 2.0 手写客户端）。

端点：https://mcp.tianyancha.com/v1
鉴权：HTTP 头 Authorization: <tyc_*** API Key>（与 AI 平台控制台共用同一 Key）

职责边界：本模块只负责传输与协议编排——``verify``（单主体锚定）、
``search_companies``（候选检索）、``fetch_dimensions``（主体锚定 + 逐风险维度
``call_tool`` 代理）。额度检查、单次远程调用、记账全部委托 D9 单工具执行器
``tyc_quota``；维度方案（默认 12 维、覆盖校验、分页与空结果口径）见
``tyc_dimensions``；协议解析与异常见 ``tyc_mcp``。不引入 mcp SDK，零额外依赖；
可用 httpx.MockTransport 做协议级测试。
"""

from __future__ import annotations

from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.agent.tyc_dimensions import (
    DimensionFetchDeps,
    dimension_arguments,
    fetch_tyc_dimensions,
    resolve_tyc_dimensions,
)
from app.agent.tyc_mcp import (
    MAX_CANDIDATES,
    TycGatewayConfigurationError,
    TycGatewayError,
    error_text,
    parse_candidates_table,
    parse_jsonrpc_response,
)

DEFAULT_ENDPOINT = "https://mcp.tianyancha.com/v1"
MCP_PROTOCOL_VERSION = "2025-06-18"
SEARCH_COMPANIES_TOOL = "search_companies"
CALL_TOOL_NAME = "call_tool"
TYC_SOURCE_CODE = "tianyancha"


class TycGateway(Protocol):
    async def verify(self, company_name: str) -> dict[str, object]: ...

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]: ...


class McpTycGateway:
    """基于 httpx 的天眼查 MCP 客户端，实现 TycGateway 接口。"""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30,
        dimensions: object = None,
    ) -> None:
        if not api_key:
            raise TycGatewayConfigurationError("天眼查 API Key 未配置")
        self.api_key = api_key
        self.endpoint = endpoint
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        # 覆盖原始值来自 login_config.tyc_dimensions（JSON 形态）；非法整体回落默认。
        self.dimensions: tuple[str, ...] = resolve_tyc_dimensions(dimensions)

    async def verify(self, company_name: str) -> dict[str, object]:
        candidates = await self.search_companies(company_name)
        if not candidates:
            return {"status": "empty", "message": "未检索到该企业相关记录"}
        top = candidates[0]
        return {
            "status": "success",
            "company_name": top.get("name", company_name),
            "credit_code": top.get("credit_code"),
            "reg_status": top.get("reg_status"),
            "candidates": candidates[:3],
        }

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        """1 次主体锚定 + N 次维度调用；每一步都经 D9 单工具额度执行器。"""
        return await fetch_tyc_dimensions(
            company_name,
            self.dimensions,
            DimensionFetchDeps(
                anchor_tool_name=SEARCH_COMPANIES_TOOL,
                anchor_call=lambda: self.verify(company_name),
                dimension_text_call=self._dimension_text_call,
            ),
        )

    async def search_companies(
        self, query: str, *, page_size: int = MAX_CANDIDATES
    ) -> list[dict[str, object]]:
        session_id: str | None
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            session_id = await self._initialize(client)
            await self._notify_initialized(client, session_id)
            result = await self._call_tool(
                client,
                SEARCH_COMPANIES_TOOL,
                {"query": query, "page_size": page_size},
                session_id,
            )
        return parse_candidates_table(result)

    async def _dimension_text_call(self, tool_name: str, company_name: str) -> str:
        """经 call_tool 代理调用单个维度工具并返回原始文本（list 工具显式分页）。"""
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            session_id = await self._initialize(client)
            await self._notify_initialized(client, session_id)
            return await self._call_tool(
                client,
                CALL_TOOL_NAME,
                {
                    "company_name": company_name,
                    "tool_name": tool_name,
                    "arguments": dimension_arguments(tool_name),
                },
                session_id,
            )

    async def _initialize(self, client: httpx.AsyncClient) -> str | None:
        response = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "supplier-risk-agent", "version": "0.1.0"},
                },
            },
            session_id=None,
        )
        session_id = response.headers.get("Mcp-Session-Id")
        return session_id if isinstance(session_id, str) and session_id else None

    async def _notify_initialized(
        self, client: httpx.AsyncClient, session_id: str | None
    ) -> None:
        await self._post(
            client,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=session_id,
        )

    async def _call_tool(
        self,
        client: httpx.AsyncClient,
        name: str,
        arguments: dict[str, object],
        session_id: str | None,
    ) -> str:
        response = await self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            session_id=session_id,
        )
        body = parse_jsonrpc_response(response.text)
        if body.get("error"):
            raise TycGatewayError(error_text(body["error"]))
        result = body.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            raise TycGatewayError("天眼查工具调用失败")
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        return text
        raise TycGatewayError("天眼查返回内容为空")

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        *,
        session_id: str | None,
    ) -> httpx.Response:
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        try:
            response = await client.post(self.endpoint, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TycGatewayError("天眼查 MCP 网络请求失败") from exc

        if response.status_code in (401, 403):
            raise TycGatewayError("天眼查鉴权失败，请检查 API Key")
        if response.status_code == 429:
            raise TycGatewayError("天眼查调用额度超限（quota_exceeded），请稍后重试")
        if response.status_code >= 400:
            raise TycGatewayError(f"天眼查 MCP 请求失败（HTTP {response.status_code}）")
        return response


def build_tyc_gateway(
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    session: Session | None = None,
) -> TycGateway:
    """按配置构建网关：无可用密钥时返回占位实现（不调用、不计费）。

    密钥优先级：显式 ``api_key`` 参数 > 信息源控制台加密存库（传入 session 时）。
    启用/停用状态由控制台 enabled 字段控制，本函数只负责取到可用密钥。
    当密钥来自控制台时，同时读取 ``login_config.tyc_dimensions`` 作为维度覆盖
    （非法覆盖由 ``resolve_tyc_dimensions`` 记录警告并整体回落默认 12 维）。
    """
    from sqlalchemy import select

    from app.signals.models import DataSource
    from app.signals.secret_store import decrypt_secret

    key = api_key
    active_endpoint = endpoint or DEFAULT_ENDPOINT
    raw_dimensions: object = None
    if not key and session is not None:
        source = session.scalar(
            select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
        )
        if source is not None:
            if isinstance(source.login_config, dict):
                raw_dimensions = source.login_config.get("tyc_dimensions")
            if source.api_key_encrypted:
                db_key = decrypt_secret(source.api_key_encrypted)
                if db_key:
                    key = db_key
                    if source.endpoint_url:
                        active_endpoint = source.endpoint_url
    if not key:
        return UnconfiguredTycGateway()
    return McpTycGateway(
        key,
        endpoint=active_endpoint,
        transport=transport,
        dimensions=raw_dimensions,
    )


class UnconfiguredTycGateway:
    async def verify(self, company_name: str) -> dict[str, object]:
        return {
            "status": "not_configured",
            "message": "天眼查网关未配置：请在信息源控制台配置运行密钥",
        }

    async def fetch_dimensions(self, company_name: str) -> dict[str, object]:
        return {
            "status": "not_configured",
            "company_name": company_name,
            "credit_code": None,
            "reg_status": None,
            "dimensions": {},
        }
