"""天眼查批量核查测试共用支持（非 pytest 用例模块）。

背景（Wave 1 事务边界）：D9 额度执行器使用独立 ``SessionLocal``，测试里
对天眼查信息源的额度/密钥/维度的修改必须**真提交**才对执行器可见；同时
计费行会跨连接残留，必须按用例清理，避免跨用例状态泄漏。

本模块提供：
- 提交态配置/快照恢复/计费表清理；
- ``MultidimMcpStub``：确定性多维度 MCP MockTransport stub（JSON-RPC），
  支持按公司名配置锚定与维度结果；**只走内存传输，绝不发起真实网络调用**。

所有测试文件通过本模块组装，避免各自复制导致边界漂移。
"""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.budget import get_tyc_usage
from app.agent.models import TycUsageRecord
from app.agent.tyc_gateway import McpTycGateway
from app.database import engine
from app.signals.models import DataSource

TYC_SOURCE_CODE = "tianyancha"
TEST_TYC_KEY = "tyc_batch_support_test_key"

_CANDIDATE_TEMPLATE = (
    "| # | 企业名称 | 统一社会信用代码 | 登记状态 |\n"
    "| --- | --- | --- | --- |\n"
    "| 1 | {company} | 91310000MDTEST0001 | 存续 |\n"
)
_EMPTY_CANDIDATES = "未查询到相关记录"
_EMPTY_DIMENSION_TEXT = "> 空结果：未发现该维度记录"


def truncate_committed_tyc_usage() -> None:
    """真提交清空计费表：额度执行器使用独立 Session，需跨连接可见。"""
    with engine.begin() as connection:
        connection.execute(delete(TycUsageRecord))


def configure_committed_tyc(
    *,
    daily_limit: int,
    monthly_limit: int,
    dimensions: object = None,
    enabled: bool = True,
    api_key: str | None = TEST_TYC_KEY,
) -> None:
    """真提交地配置天眼查测试源（启用、密钥、额度、可选维度覆盖）。"""
    from app.signals.secret_store import encrypt_secret

    with Session(engine) as setup:
        source = setup.scalar(select(DataSource).where(DataSource.code == TYC_SOURCE_CODE))
        assert source is not None, "迁移应已注册 tianyancha 信息源"
        source.enabled = enabled
        if api_key is None:
            source.api_key_encrypted = None
            source.api_key_hash = None
            source.api_key_last4 = None
        else:
            source.api_key_encrypted = encrypt_secret(api_key)
            source.api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            source.api_key_last4 = "test"
        config: dict[str, object] = {
            "mode": "on_demand",
            "secret_source": "console",
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
        }
        if dimensions is not None:
            config["tyc_dimensions"] = dimensions
        source.login_config = config
        setup.commit()


def snapshot_tyc_source() -> tuple[object, ...]:
    """快照天眼查提交态配置（供用例结束后恢复）。"""
    with Session(engine) as snapshot:
        source = snapshot.scalar(
            select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
        )
        assert source is not None
        return (
            source.enabled,
            source.api_key_encrypted,
            source.api_key_hash,
            source.api_key_last4,
            source.login_config,
            source.endpoint_url,
        )


def restore_tyc_source(original: tuple[object, ...]) -> None:
    with Session(engine) as restore:
        source = restore.scalar(
            select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
        )
        assert source is not None
        (
            source.enabled,
            source.api_key_encrypted,
            source.api_key_hash,
            source.api_key_last4,
            source.login_config,
            source.endpoint_url,
        ) = original
        restore.commit()


def committed_tyc_rows() -> list[tuple[str, str, str]]:
    """独立连接回读计费行：(tool_name, company_name, status)。"""
    with Session(engine) as probe:
        rows = probe.execute(
            select(
                TycUsageRecord.tool_name,
                TycUsageRecord.company_name,
                TycUsageRecord.status,
            ).order_by(TycUsageRecord.id)
        ).all()
    return [(row.tool_name, row.company_name, row.status) for row in rows]


def committed_tyc_daily_used() -> int:
    with Session(engine) as probe:
        return get_tyc_usage(probe).daily_used


class CompanyStub(NamedTuple):
    """单家公司在 stub 中的确定性行为。"""

    anchor: str = "success"  # success | empty
    anchor_error: bool = False  # search_companies 返回 isError（远程失败）
    dim_status: str = "success"  # success | empty
    error_tools: frozenset[str] = frozenset()  # 指定维度 isError


class MultidimMcpStub:
    """确定性多维度 MCP stub：JSON-RPC initialize + search_companies + call_tool。"""

    def __init__(
        self,
        per_company: dict[str, CompanyStub] | None = None,
        *,
        default: CompanyStub | None = None,
    ) -> None:
        self.per_company = per_company or {}
        self.default = default or CompanyStub()
        self.calls: list[tuple[str, str]] = []  # (company_name, tool_name)

    def _config(self, company: str) -> CompanyStub:
        return self.per_company.get(company, self.default)

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
                headers={"Mcp-Session-Id": "tyc-stub-session"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method != "tools/call":
            return httpx.Response(400)
        params = payload.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        assert isinstance(name, str) and isinstance(arguments, dict)
        if name == "search_companies":
            company = str(arguments.get("query", ""))
            self.calls.append((company, "search_companies"))
            config = self._config(company)
            if config.anchor_error:
                return _is_error_response("天眼查工具调用失败（stub 注入）")
            if config.anchor == "empty":
                return _text_response(_EMPTY_CANDIDATES)
            return _text_response(_CANDIDATE_TEMPLATE.format(company=company))
        assert name == "call_tool", name
        company = str(arguments.get("company_name", ""))
        tool_name = str(arguments.get("tool_name", ""))
        self.calls.append((company, tool_name))
        config = self._config(company)
        if tool_name in config.error_tools:
            return _is_error_response(f"{tool_name} 调用失败（stub 注入）")
        if config.dim_status == "empty":
            return _text_response(_EMPTY_DIMENSION_TEXT)
        return _text_response(
            f"# {tool_name}：{company}\n\n"
            f"- tool: `{tool_name}`\n\n"
            f"> 摘要：{company} {tool_name} 测试记录\n"
        )

    def gateway(self, dimensions: object = None) -> McpTycGateway:
        """构造接入本 stub 的真实网关；dimensions 用于对齐用例的维度覆盖。"""
        return McpTycGateway(
            TEST_TYC_KEY,
            transport=httpx.MockTransport(self.handler),
            dimensions=dimensions,
        )


def _text_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        },
    )


def _is_error_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": text}], "isError": True},
        },
    )
