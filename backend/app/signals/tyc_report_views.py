"""采集记录列表/详情的受控派生视图。

合同：天眼查多维度核查信号的 ``raw_data`` 等于 ``TycRiskReport`` 的
``model_dump(mode="json")``（见 ``app.agent.tyc_batch_supplier``）。本模块只做
确定性、无副作用的派生：

- 列表 ``summary``：报告信号取 ``render_key_summary``，其余回落 content；
  统一按字符数（Unicode 码点）截断，空值给稳定占位。
- 详情 ``report``：仅当 ``raw_data`` 可被 ``TycRiskReport.model_validate``
  且 JSON 编码不超过 256KB 时返回；超限不尝试构造返回体，改为显式截断标记。
- 不向任何响应暴露 ORM ``raw_data`` 全量。
"""

import json

from pydantic import ValidationError

from app.agent.tyc_analysis_context import report_payload
from app.agent.tyc_report import TycRiskReport, render_key_summary

#: 列表摘要统一最大字符数（码点）。证据 todo10.md 说明取值理由。
SUMMARY_MAX_CHARS = 240
#: raw_data JSON 编码（UTF-8）上限：超过则不返回 report，改显式截断标记。
MAX_RAW_DATA_BYTES = 256 * 1024
#: 超限时详情 content 保留的正文最大字符数（另加截断说明）。
DETAIL_CONTENT_MAX_CHARS = 4000
#: 无可用摘要时的稳定占位。
EMPTY_SUMMARY = "（暂无摘要）"
#: 超限截断说明后缀。
TRUNCATION_SUFFIX = "…（原始报告过大，正文已截断）"

_REPORT_KIND = "supplier_profile"


def clip_text(text: str, limit: int) -> str:
    """按 Unicode 码点截断（不切分代理对），超限追加单字符省略号。"""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return "…"
    return cleaned[: limit - 1].rstrip() + "…"


def extract_report(raw_data: object) -> TycRiskReport | None:
    """把 ORM ``raw_data`` 解析为报告；非报告或 malformed 一律返回 None。

    只剔除已知私有上下文字段（``report_payload``）：其他未知键仍由
    ``extra="forbid"`` 拒绝，因此这里绝不会把损坏报告当成正常报告。
    """
    payload = report_payload(raw_data)
    if payload is None or payload.get("report_kind") != _REPORT_KIND:
        return None
    try:
        return TycRiskReport.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        return None


def raw_data_json_bytes(raw_data: object) -> int:
    """``raw_data`` 以 JSON（UTF-8、非 ASCII 直出）编码后的字节数；不可编码按 0。

    私有分析上下文本就不进入 API 响应（报告只回 ``TycRiskReport``），因此仍按完整
    ``raw_data`` 计量：新增私有上下文只会让体积检查更保守，不会放松可见报告边界。
    """
    if raw_data is None:
        return 0
    try:
        encoded = json.dumps(raw_data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return 0
    return len(encoded.encode("utf-8"))


def derive_summary(raw_data: object, content: str) -> str:
    """列表摘要：报告取重点摘要，其余回落 content，统一截断并处理空值。"""
    report = extract_report(raw_data)
    text = render_key_summary(report) if report is not None else content
    return clip_text(text, SUMMARY_MAX_CHARS) or EMPTY_SUMMARY


def build_detail_content(content: str, *, truncated: bool) -> str:
    """详情正文：正常返回全文；超限时按上限截断并追加说明。"""
    if not truncated:
        return content
    clipped = content[:DETAIL_CONTENT_MAX_CHARS].rstrip()
    return f"{clipped}{TRUNCATION_SUFFIX}"
