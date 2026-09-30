"""天眼查 MCP 协议层：错误类型与响应解析（从 ``tyc_gateway`` 拆出以控制模块规模）。

- ``TycGatewayError`` / ``TycGatewayConfigurationError``：网关统一异常；
- JSON-RPC 响应兼容两种响应体：纯 JSON 与 SSE 流（``event: message`` + ``data:``）；
- ``search_companies`` 返回 Markdown 候选表，按列名定位避免列顺序变化。

本模块不做任何网络调用、不感知额度与维度；传输与编排由 ``tyc_gateway`` 负责。
"""

from __future__ import annotations

import json

MAX_CANDIDATES = 5


class TycGatewayError(RuntimeError):
    pass


class TycGatewayConfigurationError(TycGatewayError):
    pass


def parse_jsonrpc_response(text: str) -> dict[str, object]:
    """兼容两种响应体：纯 JSON 与 SSE 流（event: message\\ndata: {...}）。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        return as_object(json.loads(stripped))
    for line in stripped.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if payload:
                return as_object(json.loads(payload))
    raise TycGatewayError("天眼查 MCP 返回了无法解析的响应")


def as_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    raise TycGatewayError("天眼查 MCP 返回结构无效")


def error_text(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return f"天眼查工具错误：{message}"
        code = error.get("code")
        return f"天眼查工具错误（code={code}）"
    return "天眼查工具错误"


def parse_candidates_table(markdown_text: str) -> list[dict[str, object]]:
    """解析 search_companies 返回的 Markdown 候选表。

    表头行含「企业名称」「统一社会信用代码」「登记状态」等列；
    按列名定位索引，避免列顺序变化导致的解析错误。
    """
    lines = [line for line in markdown_text.splitlines() if line.strip().startswith("|")]
    header = split_table_row(lines[0]) if lines else []
    name_idx = column_index(header, "企业名称")
    credit_idx = column_index(header, "统一社会信用代码")
    status_idx = column_index(header, "登记状态")
    if name_idx is None:
        return []

    candidates: list[dict[str, object]] = []
    for line in lines[2:]:  # 跳过表头与分隔行
        cells = split_table_row(line)
        if len(cells) <= name_idx:
            continue
        name = cells[name_idx].strip()
        if not name or name.isdigit() or name == "企业名称":
            continue
        candidates.append(
            {
                "name": name,
                "credit_code": cells[credit_idx].strip()
                if credit_idx is not None
                else None,
                "reg_status": cells[status_idx].strip()
                if status_idx is not None
                else None,
            }
        )
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def column_index(header: list[str], column_name: str) -> int | None:
    for index, cell in enumerate(header):
        if cell.strip() == column_name:
            return index
    return None
