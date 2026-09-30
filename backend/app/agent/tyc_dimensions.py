"""天眼查风险维度方案（Todo 5）：默认清单、覆盖校验与逐工具取数编排。

依据 Todo 1 实测证据（``.omo/evidence/tyc-multidim-risk-llm/todo1.md``）：

- ``DEFAULT_TYC_DIMENSIONS`` 精确等于 ``final_dimension_set``（12 项、去重、严格按序）；
- ``KNOWN_TYC_DIMENSIONS`` 为 17 项实测可用候选池（默认 12 + 5 个扩展）；
- 运营可用 ``data_sources.login_config.tyc_dimensions`` 覆盖，但必须去重、1..12 项且
  每项是已知工具名，非法覆盖**整体**回落代码默认（绝不部分接受）并记录警告；
- 11 个 list 工具必须显式 ``page``/``page_size``，否则 ``isError=true`` 且白耗一维；
- 空结果判定按**结构**：无表格数据行/条目/「共有 N>0 条」声明且命中空结果文案才判
  ``empty``（不计费）；「有记录段 + 子段空结果说明」混合响应仍为 ``success``。

``fetch_tyc_dimensions`` 编排「1 次主体锚定 + N 次维度调用」：每一步都经 D9 单工具
执行器 ``execute_tyc_tool_with_quota``（独立短事务 + 额度锁 + 锁内重读，禁止批内
余额快照）；单维失败隔离；``quota_exhausted``/``busy``/``not_configured``（来源停用
或运行密钥撤销）后停止继续调用，并把未执行维度按同态标记。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.agent.tyc_quota import (
    RemoteCall,
    TycQuotaExecutionResult,
    TycQuotaOutcome,
    execute_tyc_tool_with_quota,
)

logger = logging.getLogger("scheduler")

DimensionStatus = Literal[
    "success", "empty", "error", "quota_exhausted", "busy", "not_configured"
]
DimensionTextCall = Callable[[str, str], Awaitable[str]]

MAX_TYC_DIMENSIONS = 12

# Todo 1 §3：final_dimension_set（严格按序；此处为唯一代码默认来源）。
DEFAULT_TYC_DIMENSIONS: tuple[str, ...] = (
    "get_risk_overview",
    "get_judicial_case",
    "get_default_event_info",
    "get_hearing_notice",
    "get_court_notice",
    "get_administrative_license",
    "get_random_check",
    "get_spot_check_info",
    "get_credit_evaluation",
    "get_shell_company_check",
    "get_change_records",
    "get_actual_controller",
)

# Todo 1 §3：未入选 12 维的 5 个实测可用候选（允许被覆盖选用的扩展池）。
_EXTRA_TYC_DIMENSIONS: tuple[str, ...] = (
    "get_beneficial_owners",
    "get_external_investments",
    "get_shareholder_info",
    "get_company_registration_info",
    "get_equity_ratio",
)

KNOWN_TYC_DIMENSIONS: tuple[str, ...] = DEFAULT_TYC_DIMENSIONS + _EXTRA_TYC_DIMENSIONS

# Todo 1 §3「需 page/page_size = 是」的 11 个 list 工具；非 list 工具不得凭空加参数。
_LIST_TYC_DIMENSIONS: frozenset[str] = frozenset(
    {
        "get_judicial_case",
        "get_default_event_info",
        "get_hearing_notice",
        "get_court_notice",
        "get_administrative_license",
        "get_random_check",
        "get_credit_evaluation",
        "get_change_records",
        "get_beneficial_owners",
        "get_external_investments",
        "get_shareholder_info",
    }
)

DIMENSION_PAGE = 1
DIMENSION_PAGE_SIZE = 20

# Todo 1 实测的空结果文本形态（如「空结果：未发现该维度记录」「未查询到相关记录」）。
_EMPTY_TEXT_MARKERS: tuple[str, ...] = (
    "空结果",
    "未发现",
    "未查询到",
    "无记录",
    "无相关记录",
)

# 结构判定：Markdown 表格分隔行单元格 / 项目符号或编号条目 / 「共有 N 条」摘要声明。
_SEPARATOR_CELL = re.compile(r":?-{1,}:?")
_RECORD_ITEM = re.compile(r"(?:[-*+]\s+|\d+[.)、]\s+)")
_SUMMARY_RECORD_COUNT = re.compile(r"共有\s*(\d+)\s*条")

_STATUS_BY_OUTCOME: dict[TycQuotaOutcome, DimensionStatus] = {
    TycQuotaOutcome.SUCCESS_WITH_RECORDS: "success",
    TycQuotaOutcome.EMPTY: "empty",
    TycQuotaOutcome.ERROR: "error",
    TycQuotaOutcome.QUOTA_EXHAUSTED: "quota_exhausted",
    TycQuotaOutcome.BUSY: "busy",
    TycQuotaOutcome.NOT_CONFIGURED: "not_configured",
}

# 终止态：出现即不再发起后续维度调用（防超发）；剩余维度按同态标记。
_TERMINAL_OUTCOMES: frozenset[TycQuotaOutcome] = frozenset(
    {
        TycQuotaOutcome.QUOTA_EXHAUSTED,
        TycQuotaOutcome.BUSY,
        TycQuotaOutcome.NOT_CONFIGURED,
    }
)

_SKIPPED_MESSAGES: dict[TycQuotaOutcome, str] = {
    TycQuotaOutcome.QUOTA_EXHAUSTED: "天眼查额度耗尽，该维度未调用",
    TycQuotaOutcome.BUSY: "天眼查额度繁忙，该维度未调用",
    TycQuotaOutcome.NOT_CONFIGURED: "天眼查未启用，该维度未调用",
}


@dataclass(frozen=True)
class DimensionFetchDeps:
    """维度取数的远程调用注入点（由网关注入；测试可用受控 fake 替换）。"""

    anchor_tool_name: str
    anchor_call: RemoteCall
    dimension_text_call: DimensionTextCall


def resolve_tyc_dimensions(raw: object) -> tuple[str, ...]:
    """校验运营覆盖的维度清单；任何非法即整体回落代码默认（绝不部分接受）。"""
    if raw is None:
        return DEFAULT_TYC_DIMENSIONS
    if not isinstance(raw, (list, tuple)):
        logger.warning("天眼查维度覆盖非法（应为字符串列表），整体回落默认 12 维")
        return DEFAULT_TYC_DIMENSIONS
    if not 1 <= len(raw) <= MAX_TYC_DIMENSIONS:
        logger.warning("天眼查维度覆盖数量非法（%d 项），整体回落默认 12 维", len(raw))
        return DEFAULT_TYC_DIMENSIONS
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in KNOWN_TYC_DIMENSIONS:
            logger.warning("天眼查维度覆盖包含未知工具名 %r，整体回落默认 12 维", item)
            return DEFAULT_TYC_DIMENSIONS
        names.append(item)
    if len(set(names)) != len(names):
        logger.warning("天眼查维度覆盖存在重复项，整体回落默认 12 维")
        return DEFAULT_TYC_DIMENSIONS
    return tuple(names)


def dimension_arguments(tool_name: str) -> dict[str, object]:
    """按 Todo 1 证据为 list 工具显式分页；非 list 工具不添加任何参数。"""
    if tool_name in _LIST_TYC_DIMENSIONS:
        return {"page": DIMENSION_PAGE, "page_size": DIMENSION_PAGE_SIZE}
    return {}


def is_empty_dimension_text(text: str) -> bool:
    """结构化「无记录」判定：无实际记录行且命中空结果文案（或整段空白）才为 empty。

    记录行 = 表格数据行（跳过表头/分隔行）、项目符号/编号条目、或「共有 N>0 条」摘要。
    """
    stripped = text.strip()
    if not stripped:
        return True
    lines = stripped.splitlines()
    for index, line in enumerate(lines):
        current = line.strip()
        if current.startswith("|"):
            if _is_separator_row(current):
                continue
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if _is_separator_row(following):
                continue  # 表头行（后随分隔行）
            return False
        summary_count = _SUMMARY_RECORD_COUNT.search(current)
        if summary_count is not None and int(summary_count.group(1)) > 0:
            return False
        if (
            not current.startswith((">", "- tool:"))
            and _RECORD_ITEM.match(current) is not None
        ):
            return False
    return any(marker in stripped for marker in _EMPTY_TEXT_MARKERS)


def _is_separator_row(line: str) -> bool:
    """Markdown 表格分隔行：每个单元格都是 ``-``/``:`` 组合（如 ``|---|---|``）。"""
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


async def fetch_tyc_dimensions(
    company_name: str,
    dimensions: Sequence[str],
    deps: DimensionFetchDeps,
) -> dict[str, object]:
    """主体锚定 + 逐维度取数，返回 Todo 5 固定合同。

    返回 ``{"status", "company_name", "credit_code", "reg_status", "dimensions"}``；
    顶层 ``status`` 描述主体锚定结果（``success``/``empty``/``error``/
    ``quota_exhausted``/``busy``），锚定失败时 ``dimensions`` 为空。锚定成功后逐维执行，
    单维 error/empty 被隔离在 ``dimensions[tool_name]["status"]``，不中断其余维度。
    """
    anchor = await execute_tyc_tool_with_quota(
        deps.anchor_tool_name, company_name, deps.anchor_call
    )
    anchor_payload = (
        anchor.payload
        if anchor.outcome is TycQuotaOutcome.SUCCESS_WITH_RECORDS
        else None
    )
    found: dict[str, object] = {}
    if anchor_payload is None:
        return {
            "status": _STATUS_BY_OUTCOME[anchor.outcome],
            "company_name": company_name,
            "credit_code": None,
            "reg_status": None,
            "dimensions": found,
        }
    payload_name = anchor_payload.get("company_name")
    if isinstance(payload_name, str) and payload_name:
        resolved_name = payload_name
    else:
        resolved_name = company_name
    status, found = await _collect_dimensions(deps, dimensions, resolved_name)
    return {
        "status": status,
        "company_name": resolved_name,
        "credit_code": anchor_payload.get("credit_code"),
        "reg_status": anchor_payload.get("reg_status"),
        "dimensions": found,
    }


async def _collect_dimensions(
    deps: DimensionFetchDeps,
    dimensions: Sequence[str],
    company_name: str,
) -> tuple[DimensionStatus, dict[str, object]]:
    """逐维度调用执行器；返回（顶层状态，维度条目表）。"""
    found: dict[str, object] = {}
    for position, tool_name in enumerate(dimensions):
        execution = await execute_tyc_tool_with_quota(
            tool_name,
            company_name,
            _dimension_remote_call(deps, tool_name, company_name),
        )
        found[tool_name] = _dimension_entry(execution)
        if execution.outcome in _TERMINAL_OUTCOMES:
            _mark_skipped(dimensions[position + 1 :], execution.outcome, found)
            return _STATUS_BY_OUTCOME[execution.outcome], found
    return "success", found


def _dimension_remote_call(
    deps: DimensionFetchDeps, tool_name: str, company_name: str
) -> RemoteCall:
    """构造单维度 remote_call：把原始文本按真实无记录文案分类为 empty/success。"""

    async def call() -> dict[str, object]:
        text = await deps.dimension_text_call(tool_name, company_name)
        if is_empty_dimension_text(text):
            return {"status": "empty", "raw": text, "message": None}
        return {"status": "success", "raw": text, "message": None}

    return call


def _dimension_entry(execution: TycQuotaExecutionResult) -> dict[str, object]:
    """执行结果 → 维度条目 ``{status, raw, message}``。"""
    entry: dict[str, object] = {
        "status": _STATUS_BY_OUTCOME[execution.outcome],
        "raw": None,
        "message": execution.message,
    }
    if execution.payload is not None:
        raw = execution.payload.get("raw")
        if isinstance(raw, str):
            entry["raw"] = raw
        payload_message = execution.payload.get("message")
        if entry["message"] is None and isinstance(payload_message, str):
            entry["message"] = payload_message
    return entry


def _mark_skipped(
    remaining: Sequence[str], outcome: TycQuotaOutcome, found: dict[str, object]
) -> None:
    """终止态后：剩余维度按同态标记为「未调用」，不产生远程调用与记账。"""
    status = _STATUS_BY_OUTCOME[outcome]
    message = _SKIPPED_MESSAGES[outcome]
    for tool_name in remaining:
        found[tool_name] = {"status": status, "raw": None, "message": message}
