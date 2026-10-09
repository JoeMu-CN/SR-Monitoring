"""天眼查画像分析上下文的纯函数测试（不访问网络/数据库）。

覆盖预算契约：全部维度元数据优先保留、成功维度基础预算、稳定顺序扩展、
超限显式采样而不丢尾部维度、原文脱敏、原文缺失标记、查询未完成标记。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.agent.tyc_analysis_context import (
    _MAX_CONTEXT_CHARS,
    ANALYSIS_CONTEXT_VERSION,
    build_analysis_context,
    is_supplier_profile_context,
    load_analysis_context,
    render_analysis_context,
    report_payload,
)
from app.agent.tyc_report import TycRiskReport, build_risk_report

SUPPLIER_CODE = "SUP-CTX-001"
GENERATED_AT = datetime(2026, 9, 29, 4, 30, tzinfo=UTC)
# 尾部维度事实：证明原文后段能进入上下文，而非只保留前 600 字摘要。
TAIL_FACT = "判决结果：败诉，赔偿金额 320 万元，2026-09-20 结案"


def _entry(status: str, raw: str | None, message: str | None = None) -> dict[str, object]:
    return {"status": status, "raw": raw, "message": message}


def _results(dimensions: dict[str, object]) -> dict[str, object]:
    return {
        "status": "success",
        "company_name": "上下文测试有限公司",
        "credit_code": "91330000CTXTEST001",
        "reg_status": "存续",
        "dimensions": dimensions,
    }


def _build(results: dict[str, object]) -> tuple[TycRiskReport, object]:
    report = build_risk_report(results, supplier_code=SUPPLIER_CODE, generated_at=GENERATED_AT)
    return report, build_analysis_context(results, report=report)


def _raw_judicial(tail_fact: str = TAIL_FACT, filler: int = 0) -> str:
    body = "\n".join(
        [
            "# 风险合规：上下文测试有限公司",
            "",
            "- tool: `get_judicial_case`",
            "",
            "> 摘要：该查询实体共有1条司法案件记录。",
            "",
            "### 明细（1 条）",
            "",
            "| # | 案号 | 案件身份 | 案由 | 审理时间 |",
            "|---|---|---|---|---|",
        ]
    )
    padding = "\n".join(f"| {index} | 记录{index} | - | - | - |" for index in range(filler))
    return f"{body}\n{padding}\n{tail_fact}\n"


def test_context_keeps_report_metadata_for_every_returned_dimension() -> None:
    """全部实际返回维度都进入上下文（含非成功维度），元数据不被预算裁掉。"""
    report, context = _build(
        _results(
            {
                "get_risk_overview": _entry("success", "# 风险总览\n\n> 摘要：警示 1 条。\n"),
                "get_judicial_case": _entry("success", _raw_judicial()),
                "get_default_event_info": _entry("error", None, "接口超时"),
                "get_hearing_notice": _entry("empty", None, "未发现记录"),
            }
        )
    )

    assert [item.key for item in context.dimensions] == [
        finding.key for finding in report.dimensions
    ]
    assert [item.status.value for item in context.dimensions] == [
        "success",
        "success",
        "error",
        "empty",
    ]
    assert context.incomplete_dimensions == ["get_default_event_info"]
    assert context.query_incomplete is True
    assert context.context_version == ANALYSIS_CONTEXT_VERSION
    assert context.company_name == "上下文测试有限公司"
    assert context.generated_at == GENERATED_AT


def test_context_excerpt_carries_tail_fact_not_only_summary() -> None:
    """关键事实位于原文尾部时仍进入上下文（此前只有 600 字摘要）。"""
    _, context = _build(
        _results({"get_judicial_case": _entry("success", _raw_judicial(filler=40))})
    )

    excerpt = context.dimensions[0].raw_excerpt
    assert excerpt is not None
    assert TAIL_FACT in excerpt
    assert context.dimensions[0].summary


def test_context_excerpt_state_is_complete_when_under_base_budget() -> None:
    _, context = _build(
        _results({"get_judicial_case": _entry("success", "# 司法\n\n> 摘要：1 条记录。\n")})
    )

    assert context.dimensions[0].raw_state == "complete"


def test_missing_raw_is_marked_unavailable() -> None:
    """成功但原文缺失、失败与非成功维度都标 unavailable，不伪造原文。"""
    _, context = _build(
        _results(
            {
                "get_risk_overview": _entry("success", None),
                "get_default_event_info": _entry("busy", None),
            }
        )
    )

    assert [item.raw_state for item in context.dimensions] == [
        "unavailable",
        "unavailable",
    ]
    assert all(item.raw_excerpt is None for item in context.dimensions)


def test_context_masks_sensitive_numbers_in_raw_excerpt() -> None:
    _, context = _build(
        _results(
            {
                "get_judicial_case": _entry(
                    "success",
                    "# 司法\n\n> 摘要：联系 13800138000，证件 330203199003074512。\n",
                )
            }
        )
    )

    excerpt = context.dimensions[0].raw_excerpt
    assert excerpt is not None
    assert "13800138000" not in excerpt
    assert "330203199003074512" not in excerpt
    assert "[已脱敏]" in excerpt


def test_long_report_stays_within_budget_and_keeps_tail_dimensions() -> None:
    """超长原文下整体 JSON 有界，且尾部维度不会因前缀裁切而消失。"""
    dimensions: dict[str, object] = {
        "get_risk_overview": _entry("success", _raw_judicial(filler=200)),
        "get_judicial_case": _entry("success", _raw_judicial(filler=200)),
        "get_hearing_notice": _entry("success", _raw_judicial(filler=200)),
        "get_court_notice": _entry("success", _raw_judicial(filler=200)),
        "get_change_records": _entry("success", _raw_judicial(filler=200)),
        "get_actual_controller": _entry("success", _raw_judicial(filler=200)),
        "get_administrative_license": _entry("success", _raw_judicial(filler=200)),
        "get_random_check": _entry("success", _raw_judicial(filler=200)),
        "get_spot_check_info": _entry("success", _raw_judicial(filler=200)),
        "get_credit_evaluation": _entry("success", _raw_judicial(filler=200)),
    }
    _, context = _build(_results(dimensions))

    rendered = render_analysis_context(context)
    assert len(rendered) <= _MAX_CONTEXT_CHARS
    # 维度按报告固定顺序全部保留：末尾维度不会因全局前缀裁切而消失或丢片段。
    assert [item.key for item in context.dimensions] == [
        finding.key
        for finding in build_risk_report(
            _results(dimensions), supplier_code=SUPPLIER_CODE, generated_at=GENERATED_AT
        ).dimensions
    ]
    assert context.dimensions[-1].key == "get_actual_controller"
    assert context.dimensions[-1].raw_excerpt is not None
    assert any(item.raw_state == "sampled" for item in context.dimensions)


def test_sampled_excerpt_keeps_head_and_tail_with_explicit_marker() -> None:
    """采样片段保留首尾并显式标记省略，尾部事实不丢。"""
    _, context = _build(
        _results({"get_judicial_case": _entry("success", _raw_judicial(filler=200))})
    )

    excerpt = context.dimensions[0].raw_excerpt
    assert excerpt is not None
    assert context.dimensions[0].raw_state == "sampled"
    assert "中段采样省略" in excerpt
    assert excerpt.endswith(TAIL_FACT)


def test_anchor_failure_marks_query_incomplete() -> None:
    """锚定未成功时即使维度齐全也标记查询未完成。"""
    results = _results({"get_risk_overview": _entry("success", "# 风险\n\n> 摘要：1 条。\n")})
    results["status"] = "empty"

    report = build_risk_report(
        results, supplier_code=SUPPLIER_CODE, generated_at=GENERATED_AT
    )
    context = build_analysis_context(results, report=report)

    assert context.query_incomplete is True


def test_report_payload_strips_only_known_private_field() -> None:
    """共享提取只移除私有上下文字段；其他未知键保留给 extra=forbid 拒绝。"""
    _, context = _build(
        _results({"get_risk_overview": _entry("success", "# 风险\n\n> 摘要：1 条。\n")})
    )
    raw_data = {
        "report_kind": "supplier_profile",
        "company_name": "上下文测试有限公司",
        "analysis_context": context.model_dump(mode="json"),
        "unexpected": 1,
    }

    payload = report_payload(raw_data)

    assert payload is not None
    assert "analysis_context" not in payload
    assert payload["unexpected"] == 1


def test_load_analysis_context_round_trips_and_rejects_damaged() -> None:
    _, context = _build(
        _results({"get_risk_overview": _entry("success", "# 风险\n\n> 摘要：1 条。\n")})
    )

    loaded = load_analysis_context(
        {"analysis_context": context.model_dump(mode="json")}
    )
    damaged = load_analysis_context({"analysis_context": {"context_version": "x"}})
    missing = load_analysis_context({"report_kind": "supplier_profile"})

    assert loaded == context
    assert damaged is None
    assert missing is None


def test_is_supplier_profile_context_is_machine_routable() -> None:
    _, context = _build(
        _results({"get_risk_overview": _entry("success", "# 风险\n\n> 摘要：1 条。\n")})
    )
    rendered = render_analysis_context(context)

    assert is_supplier_profile_context(rendered) is True
    assert is_supplier_profile_context("受大风影响，港区停止装卸作业。") is False
    assert is_supplier_profile_context(json.dumps({"context_version": "other"})) is False
    assert is_supplier_profile_context("[1, 2]") is False