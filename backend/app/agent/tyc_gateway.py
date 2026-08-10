"""天眼查 MCP 网关（Streamable HTTP / JSON-RPC 2.0 手写客户端）。

端点：https://mcp.tianyancha.com/v1
鉴权：HTTP 头 Authorization: <tyc_*** API Key>（与 AI 平台控制台共用同一 Key）

只实现 verify_company 需要的子集：initialize 握手 → tools/call search_companies。
后续需要司法/风险明细时，按 get_company_capabilities → call_tool 契约扩展。
不引入 mcp SDK，零额外依赖；可用 httpx.MockTransport 做协议级测试。
"""

import json
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

DEFAULT_ENDPOINT = "https://mcp.tianyancha.com/v1"
MCP_PROTOCOL_VERSION = "2025-06-18"
SEARCH_COMPANIES_TOOL = "search_companies"
MAX_CANDIDATES = 5
TYC_SOURCE_CODE = "tianyancha"


class TycGateway(Protocol):
    async def verify(self, company_name: str) -> dict[str, object]: ...


class TycGatewayError(RuntimeError):
    pass


class TycGatewayConfigurationError(TycGatewayError):
    pass


class McpTycGateway:
    """基于 httpx 的天眼查 MCP 客户端，实现 TycGateway 接口。"""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if not api_key:
            raise TycGatewayConfigurationError("天眼查 API Key 未配置")
        self.api_key = api_key
        self.endpoint = endpoint
        self.transport = transport
        self.timeout_seconds = timeout_seconds

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
        return _parse_candidates_table(result)

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
                    "clientInfo": {
                        "name": "supplier-risk-agent",
                        "version": "0.1.0",
                    },
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
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
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
        body = _parse_jsonrpc_response(response.text)
        if body.get("error"):
            raise TycGatewayError(_error_text(body["error"]))
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


def _parse_jsonrpc_response(text: str) -> dict[str, object]:
    """兼容两种响应体：纯 JSON 与 SSE 流（event: message\\ndata: {...}）。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        return _as_object(json.loads(stripped))
    for line in stripped.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload:
                return _as_object(json.loads(payload))
    raise TycGatewayError("天眼查 MCP 返回了无法解析的响应")


def _as_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    raise TycGatewayError("天眼查 MCP 返回结构无效")


def _error_text(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return f"天眼查工具错误：{message}"
        code = error.get("code")
        return f"天眼查工具错误（code={code}）"
    return "天眼查工具错误"


def _parse_candidates_table(markdown_text: str) -> list[dict[str, object]]:
    """解析 search_companies 返回的 Markdown 候选表。

    表头行含「企业名称」「统一社会信用代码」「登记状态」等列；
    按列名定位索引，避免列顺序变化导致的解析错误。
    """
    lines = [line for line in markdown_text.splitlines() if line.strip().startswith("|")]
    header = _split_table_row(lines[0]) if lines else []
    name_idx = _column_index(header, "企业名称")
    credit_idx = _column_index(header, "统一社会信用代码")
    status_idx = _column_index(header, "登记状态")
    if name_idx is None:
        return []

    candidates: list[dict[str, object]] = []
    for line in lines[2:]:  # 跳过表头与分隔行
        cells = _split_table_row(line)
        if len(cells) <= name_idx:
            continue
        name = cells[name_idx].strip()
        if not name or name.isdigit() or name == "企业名称":
            continue
        candidates.append(
            {
                "name": name,
                "credit_code": cells[credit_idx].strip() if credit_idx is not None else None,
                "reg_status": cells[status_idx].strip() if status_idx is not None else None,
            }
        )
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _column_index(header: list[str], column_name: str) -> int | None:
    for index, cell in enumerate(header):
        if cell.strip() == column_name:
            return index
    return None


def build_tyc_gateway(
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    session: Session | None = None,
) -> TycGateway:
    """按配置构建网关：无可用密钥时返回占位实现（不调用、不计费）。

    密钥优先级：显式 ``api_key`` 参数 > 数据源控制台加密存库（传入 session 时）>
    非生产环境的 TYC_API_KEY 兼容回退。生产环境不读取环境变量密钥；启用/停用状态
    由控制台 enabled 字段控制，本函数只负责取到可用密钥。
    """
    from sqlalchemy import select

    from app import config
    from app.signals.models import DataSource
    from app.signals.secret_store import decrypt_secret

    key = api_key
    active_endpoint = endpoint or config.get_tyc_endpoint_fallback() or DEFAULT_ENDPOINT
    if not key and session is not None:
        source = session.scalar(
            select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
        )
        if source is not None and source.api_key_encrypted:
            db_key = decrypt_secret(source.api_key_encrypted)
            if db_key:
                key = db_key
                if source.endpoint_url:
                    active_endpoint = source.endpoint_url
    if not key:
        key = config.get_tyc_env_fallback()
    if not key:
        return UnconfiguredTycGateway()
    return McpTycGateway(key, endpoint=active_endpoint, transport=transport)


class UnconfiguredTycGateway:
    async def verify(self, company_name: str) -> dict[str, object]:
        return {
            "status": "not_configured",
            "message": "天眼查网关未配置：请在数据源控制台配置运行密钥",
        }
