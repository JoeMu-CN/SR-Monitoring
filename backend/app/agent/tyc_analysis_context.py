"""天眼查画像分析上下文（私有、版本化、类型化、脱敏有界）。

``raw_data`` 根报告只保存 ``TycRiskReport``（摘要 200 字、证据条目 40 字上限），
完整原文仅存在于 ``results.dimensions[tool].raw``；此前交给 LLM 的
``render_key_summary`` 最多 600 字，原文后半段的关键事实（案由、金额、最新状态）
不会进入模型。本模块在不落库完整原文、不改动报告既有字段的前提下，额外构造一份
私有上下文：

- ``context_version`` 显式升版，语义变更不静默影响历史数据；
- 全部维度元数据（key/name/status/risk_level/hit/summary）优先保留，不因预算消失；
- 每维度原文片段按稳定顺序分轮扩展（400 → 1200 字符）：先保证每个可片段维度拿到
  基础预算，再逐维度按稳定顺序抬升，装不下即回退，绝不盲目前缀裁切；
- 采样片段保留首尾（尾段承载最新状态与结论），中段以显式省略标记断开；
- 整个 JSON 上下文有界（按最终 JSON 编码长度计量，含元数据与转义开销）：超限时先
  收缩摘要、再整体收缩片段预算，仍不达标则把片段预算降到 0，而不是丢掉任何维度；
- 原文可得性标记 ``unavailable`` / ``complete`` / ``sampled``，未完成维度显式列出，
  供提示词禁止把「未查到」推断为「无风险」。

``raw_data`` 的私有字段名只在本模块定义（``PRIVATE_RAW_FIELDS``）：报告解析方经
``report_payload()`` 精确剔除该字段后解析，保留 ``extra="forbid"`` 对其他未知键的
拒绝语义。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from app.agent.tyc_report import (
    DimensionFinding,
    DimensionStatus,
    RiskLevel,
    TycRiskReport,
    mask_sensitive,
)

#: 上下文语义版本：升版即表示字段或预算语义变化，历史数据不自动回填。
ANALYSIS_CONTEXT_VERSION: Final[str] = "tyc-analysis-context-v1"
#: ``raw_data`` 中仅供模型消费的私有字段；报告解析方只剔除这些键。
PRIVATE_RAW_FIELD: Final[str] = "analysis_context"
PRIVATE_RAW_FIELDS: Final[frozenset[str]] = frozenset({PRIVATE_RAW_FIELD})

#: 每个成功维度的基础片段预算、单维度上限与扩展步长（字符）。
_BASE_EXCERPT_CHARS: Final[int] = 400
_MAX_EXCERPT_CHARS: Final[int] = 1200
_EXCERPT_STEP: Final[int] = 200
#: 整个 JSON 上下文上限（按最终 JSON 编码长度计量，含元数据与转义开销）。
_MAX_CONTEXT_CHARS: Final[int] = 10_000
#: 装不下时的摘要收缩档位：先削片段预算，再逐档削摘要。
_SUMMARY_CAPS: Final[tuple[int, ...]] = (200, 120, 80, 40, 0)
_OMISSION_MARKER: Final[str] = "\n…（中段采样省略）…\n"

RawAvailability = Literal["unavailable", "complete", "sampled"]

#: 已取到结论的维度状态；其余状态一律视为查询未完成。
_DONE_STATUSES: Final[frozenset[DimensionStatus]] = frozenset(
    {DimensionStatus.SUCCESS, DimensionStatus.EMPTY}
)


class AnalysisDimension(BaseModel):
    """单维度上下文：报告元数据 + 有界原文片段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    name: str
    status: DimensionStatus
    risk_level: RiskLevel | None = None
    hit: bool
    summary: str
    raw_state: RawAvailability
    raw_excerpt: str | None = None


class TycAnalysisContext(BaseModel):
    """交给 LLM 的天眼查画像上下文（私有，不进入任何报告响应）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_version: Literal["tyc-analysis-context-v1"] = ANALYSIS_CONTEXT_VERSION
    company_name: str
    supplier_code: str
    credit_code: str | None = None
    reg_status: str | None = None
    generated_at: AwareDatetime
    query_incomplete: bool
    incomplete_dimensions: list[str] = Field(default_factory=list)
    dimensions: list[AnalysisDimension]


@dataclass(frozen=True, slots=True)
class _Draft:
    """维度元数据（取自报告）与脱敏后原文（取自 results）。"""

    finding: DimensionFinding
    raw: str | None


def build_analysis_context(
    results: Mapping[str, object], *, report: TycRiskReport
) -> TycAnalysisContext:
    """从 ``results``（含原文）与已构造报告生成有界上下文。

    ``results`` 只用于取各维度原文与锚定状态：元数据一律取自 ``report``，因此上下文
    与落库报告不会漂移。确定性、无外部副作用。
    """
    drafts = _drafts(report, _dimension_entries(results))
    anchor_incomplete = results.get("status") != "success"
    for cap in _SUMMARY_CAPS:
        budgets = _excerpt_budgets(report, drafts, cap, anchor_incomplete)
        context = _assemble(report, drafts, budgets, cap, anchor_incomplete)
        if _context_chars(context) <= _MAX_CONTEXT_CHARS:
            return context
    # 元数据本身已超上限：丢弃全部片段。元数据优先，维度不得消失。
    return _assemble(
        report, drafts, [0] * len(drafts), _SUMMARY_CAPS[-1], anchor_incomplete
    )


def load_analysis_context(raw_data: object) -> TycAnalysisContext | None:
    """解析 ``raw_data`` 内的私有上下文；缺失或损坏返回 None（调用方回落既有文本）。"""
    if not isinstance(raw_data, Mapping):
        return None
    payload = raw_data.get(PRIVATE_RAW_FIELD)
    if payload is None:
        return None
    try:
        return TycAnalysisContext.model_validate(payload)
    except ValidationError:
        return None


def report_payload(raw_data: object) -> dict[str, object] | None:
    """剔除已知私有上下文字段后的报告字段；其他未知键原样保留（仍会被 forbid 拒绝）。"""
    if not isinstance(raw_data, Mapping):
        return None
    return {key: value for key, value in raw_data.items() if key not in PRIVATE_RAW_FIELDS}


def render_analysis_context(context: TycAnalysisContext) -> str:
    """上下文的 LLM 输入形态：确定性 JSON 文本（非 ASCII 直出）。"""
    return json.dumps(context.model_dump(mode="json"), ensure_ascii=False)


def is_supplier_profile_context(content: str) -> bool:
    """机器可判定的路由判定：``content`` 是否为天眼查画像上下文 JSON。"""
    try:
        payload = json.loads(content)
    except ValueError:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("context_version") == ANALYSIS_CONTEXT_VERSION
    )


def _dimension_entries(results: Mapping[str, object]) -> Mapping[str, object]:
    raw = results.get("dimensions")
    return raw if isinstance(raw, Mapping) else {}


def _drafts(
    report: TycRiskReport, entries: Mapping[str, object]
) -> list[_Draft]:
    return [_draft(finding, entries) for finding in report.dimensions]


def _draft(finding: DimensionFinding, entries: Mapping[str, object]) -> _Draft:
    return _Draft(finding=finding, raw=_raw_text(finding, entries))


def _raw_text(finding: DimensionFinding, entries: Mapping[str, object]) -> str | None:
    """成功维度才可能有原文；原文在此统一复用报告脱敏并去首尾空白。"""
    if finding.status is not DimensionStatus.SUCCESS:
        return None
    entry = entries.get(finding.key)
    raw = entry.get("raw") if isinstance(entry, Mapping) else None
    if not isinstance(raw, str):
        return None
    return mask_sensitive(raw).strip() or None


def _excerpt_budgets(
    report: TycRiskReport,
    drafts: Sequence[_Draft],
    summary_cap: int,
    anchor_incomplete: bool,
) -> list[int]:
    """片段预算：先给每个可片段维度基础预算，再按稳定顺序逐轮抬升到单维度上限。"""
    budgets = [_BASE_EXCERPT_CHARS if draft.raw else 0 for draft in drafts]
    if _oversized(report, drafts, budgets, summary_cap, anchor_incomplete):
        return _shrink(report, drafts, budgets, summary_cap, anchor_incomplete)
    for target in range(
        _BASE_EXCERPT_CHARS + _EXCERPT_STEP, _MAX_EXCERPT_CHARS + 1, _EXCERPT_STEP
    ):
        for index, draft in enumerate(drafts):
            if draft.raw is None:
                continue
            raised = min(target, len(draft.raw))
            if budgets[index] >= raised:
                continue
            trial = [*budgets[:index], raised, *budgets[index + 1 :]]
            if not _oversized(report, drafts, trial, summary_cap, anchor_incomplete):
                budgets = trial
    return budgets


def _shrink(
    report: TycRiskReport,
    drafts: Sequence[_Draft],
    budgets: list[int],
    summary_cap: int,
    anchor_incomplete: bool,
) -> list[int]:
    """基础预算已超上限：整体等比下调片段预算，任何维度都不被丢弃。"""
    reduced = budgets
    while any(reduced) and _oversized(
        report, drafts, reduced, summary_cap, anchor_incomplete
    ):
        reduced = [max(0, budget - _EXCERPT_STEP) for budget in reduced]
    return reduced


def _oversized(
    report: TycRiskReport,
    drafts: Sequence[_Draft],
    budgets: Sequence[int],
    summary_cap: int,
    anchor_incomplete: bool,
) -> bool:
    context = _assemble(report, drafts, budgets, summary_cap, anchor_incomplete)
    return _context_chars(context) > _MAX_CONTEXT_CHARS


def _assemble(
    report: TycRiskReport,
    drafts: Sequence[_Draft],
    budgets: Sequence[int],
    summary_cap: int,
    anchor_incomplete: bool,
) -> TycAnalysisContext:
    incomplete = [
        draft.finding.key
        for draft in drafts
        if draft.finding.status not in _DONE_STATUSES
    ]
    return TycAnalysisContext(
        company_name=report.company_name,
        supplier_code=report.supplier_code,
        credit_code=report.credit_code,
        reg_status=report.reg_status,
        generated_at=report.generated_at,
        query_incomplete=anchor_incomplete or bool(incomplete),
        incomplete_dimensions=incomplete,
        dimensions=[
            _dimension_payload(draft, budget, summary_cap)
            for draft, budget in zip(drafts, budgets, strict=True)
        ],
    )


def _dimension_payload(
    draft: _Draft, budget: int, summary_cap: int
) -> AnalysisDimension:
    finding = draft.finding
    excerpt, state = _excerpt(draft.raw, budget)
    return AnalysisDimension(
        key=finding.key,
        name=finding.name,
        status=finding.status,
        risk_level=finding.risk_level,
        hit=finding.hit,
        summary=finding.summary[:summary_cap] if summary_cap else "",
        raw_state=state,
        raw_excerpt=excerpt,
    )


def _excerpt(raw: str | None, budget: int) -> tuple[str | None, RawAvailability]:
    """按预算取原文片段：整段放得下即 complete，否则保留首尾并标记 sampled。"""
    if raw is None:
        return None, "unavailable"
    if len(raw) <= budget:
        return raw, "complete"
    if budget <= len(_OMISSION_MARKER):
        return raw[:budget], "sampled"
    keep = budget - len(_OMISSION_MARKER)
    tail = keep // 3
    head = keep - tail
    return raw[:head] + _OMISSION_MARKER + raw[len(raw) - tail :], "sampled"


def _context_chars(context: TycAnalysisContext) -> int:
    return len(render_analysis_context(context))