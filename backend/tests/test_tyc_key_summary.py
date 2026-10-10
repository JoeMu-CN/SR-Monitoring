"""``render_key_summary`` 的确定性摘要回归（纯函数，不访问数据库/网络）。

用户可见行为契约：

- 具体维度事实（司法解析/开庭公告/法院公告/失信被执行）排在前；风险总览是汇总
  维度，固定排在具体事实之后，不得因总览等级较高而抢占具体风险事实；具体维度
  之间仍按风险等级排序。
- 「已返回风险总览摘要」这类过程性文案删除；但数量、金额、风险事实词必须保留，
  不得因整句含「摘要」被丢弃。
- 数据口径差异提示（「不同口径/口径差异/口径不一致」）保留在事实之后，聚合成摘要
   所属维度的事实之后并去重，不冒充具体风险事实。
- 命中维度剥离过程性文案后没有事实：只有过程文案（无事实、无口径提示）时给如实
  提示，不谎称无风险；仅过程文案加口径提示时不制造「有风险记录」事实，只保留
  等级标签并保留口径提示。
- 无命中时单列「未取得结果」的维度数量，空/失败维度不算作已排除风险。
- 600 字上限与 600→240 的列表截断语义不变。
"""

from datetime import UTC, datetime

from app.agent.tyc_report import (
    DimensionFinding,
    DimensionStatus,
    RiskLevel,
    TycRiskReport,
    render_key_summary,
)

GENERATED_AT = datetime(2026, 9, 29, 4, 30, tzinfo=UTC)

# 用户截图中的三条入库摘要（逐字取自采集记录，不做任何改写）。
SCREENSHOT_OVERVIEW = "已返回风险总览摘要；摘要行数、上游计数与企业简介数字可能属于不同口径。"
SCREENSHOT_JUDICIAL = "明细（9条）：提供劳务者受害责任纠纷、买卖合同纠纷"
SCREENSHOT_HEARING = "该查询实体共有6条开庭公告"
# 截图场景的期望输出：具体事实在前，总览的口径说明在后。
SCREENSHOT_EXPECTED = (
    "重点命中：司法解析：明细（9条）：提供劳务者受害责任纠纷、买卖合同纠纷；"
    "开庭公告：该查询实体共有6条开庭公告；"
    "风险总览（警示）：摘要行数、上游计数与企业简介数字可能属于不同口径"
)


def _report(*findings: DimensionFinding) -> TycRiskReport:
    return TycRiskReport(
        company_name="测试企业有限公司",
        supplier_code="SUP-0001",
        credit_code=None,
        reg_status="存续",
        generated_at=GENERATED_AT,
        period_key="tyc:SUP-0001:2026-W40",
        dimensions=list(findings),
    )


def _hit(
    key: str, name: str, summary: str, level: RiskLevel | None = None
) -> DimensionFinding:
    return DimensionFinding(
        key=key,
        name=name,
        status=DimensionStatus.SUCCESS,
        risk_level=level,
        hit=True,
        summary=summary,
    )


def _missed(key: str, name: str, status: DimensionStatus, summary: str) -> DimensionFinding:
    return DimensionFinding(
        key=key, name=name, status=status, hit=False, summary=summary
    )


def _screenshot_report() -> TycRiskReport:
    return _report(
        _hit("get_risk_overview", "风险总览", SCREENSHOT_OVERVIEW, RiskLevel.ALERT),
        _hit("get_judicial_case", "司法解析", SCREENSHOT_JUDICIAL),
        _hit("get_hearing_notice", "开庭公告", SCREENSHOT_HEARING),
    )


def test_screenshot_input_puts_concrete_facts_first() -> None:
    summary = render_key_summary(_screenshot_report())

    assert summary == SCREENSHOT_EXPECTED


def test_procedural_overview_text_fabricates_no_case_count_or_role() -> None:
    summary = render_key_summary(_screenshot_report())

    # 不合并两个维度的条数，也不补出诉讼角色/金额等原文没有的信息。
    assert "15" not in summary
    assert summary.count("9") == 1
    assert summary.count("6") == 1
    assert summary.endswith("不同口径")
    for invented in ("已返回", "被告", "原告", "元"):
        assert invented not in summary


def test_overview_only_real_risk_facts_are_preserved() -> None:
    overview_facts = (
        "可用专项工具获取详情：开庭公告、法院公告；"
        "需通过风险 ID 获取详情：历史欠税公告；"
        "周边风险提示：行政处罚"
    )
    report = _report(_hit("get_risk_overview", "风险总览", overview_facts, RiskLevel.ALERT))

    summary = render_key_summary(report)

    assert summary == f"重点命中：风险总览（警示）：{overview_facts}"


def test_overview_real_facts_follow_concrete_dimensions() -> None:
    overview_facts = "制裁名单：出口管制企业，涉及 1 项"
    report = _report(
        _hit("get_risk_overview", "风险总览", overview_facts, RiskLevel.ALERT),
        _hit("get_judicial_case", "司法解析", SCREENSHOT_JUDICIAL),
    )

    summary = render_key_summary(report)

    assert summary == (
        f"重点命中：司法解析：{SCREENSHOT_JUDICIAL}；"
        f"风险总览（警示）：{overview_facts}"
    )


def test_caliber_clause_keeps_real_quantity() -> None:
    report = _report(
        _hit(
            "get_risk_overview",
            "风险总览",
            "已返回风险总览摘要；摘要行数可能属于不同口径，命中历史欠税公告 2 项",
            RiskLevel.ALERT,
        )
    )

    summary = render_key_summary(report)

    assert summary == "重点命中：风险总览（警示）：摘要行数可能属于不同口径，命中历史欠税公告 2 项"


def test_hit_without_facts_gets_honest_hint() -> None:
    report = _report(_hit("get_risk_overview", "风险总览", "已返回风险总览摘要", RiskLevel.ALERT))

    summary = render_key_summary(report)

    assert summary == "重点命中：风险总览（警示）：有风险记录，具体信息未披露"


def test_concrete_dimensions_keep_risk_level_order_within_group() -> None:
    report = _report(
        _hit("get_judicial_case", "司法解析", "买卖合同纠纷", RiskLevel.MEDIUM),
        _hit("get_hearing_notice", "开庭公告", SCREENSHOT_HEARING, RiskLevel.HIGH),
        _hit("get_court_notice", "法院公告", "送达公告", RiskLevel.LOW),
        _hit("get_risk_overview", "风险总览", "周边风险提示：行政处罚", RiskLevel.ALERT),
    )

    summary = render_key_summary(report)

    assert summary == (
        "重点命中：开庭公告（高风险）：该查询实体共有6条开庭公告；"
        "司法解析（中风险）：买卖合同纠纷；"
        "法院公告（低风险）：送达公告；"
        "风险总览（警示）：周边风险提示：行政处罚"
    )


def test_long_procedural_overview_does_not_consume_summary_budget() -> None:
    procedural_blob = "已返回风险总览摘要；" * 12
    facts = "、".join(f"案由{index}纠纷" for index in range(1, 30))
    report = _report(
        _hit("get_risk_overview", "风险总览", procedural_blob, RiskLevel.ALERT),
        _hit("get_judicial_case", "司法解析", facts),
        _hit("get_hearing_notice", "开庭公告", facts),
        _hit("get_court_notice", "法院公告", facts),
    )

    summary = render_key_summary(report)

    assert len(summary) <= 600
    assert summary.startswith("重点命中：司法解析：案由1纠纷")
    assert "已返回" not in summary


def test_no_hit_discloses_dimensions_without_result() -> None:
    report = _report(
        _missed("get_hearing_notice", "开庭公告", DimensionStatus.ERROR, "查询失败：超时"),
        _missed("get_court_notice", "法院公告", DimensionStatus.EMPTY, "未发现法院公告记录"),
    )

    summary = render_key_summary(report)

    assert summary == "未发现重点风险命中（共核查 2 个维度，另有 1 个维度未取得结果）"


def test_no_hit_without_missing_dimension_keeps_plain_wording() -> None:
    report = _report(
        _missed("get_court_notice", "法院公告", DimensionStatus.EMPTY, "未发现法院公告记录")
    )

    summary = render_key_summary(report)

    assert summary == "未发现重点风险命中（共核查 1 个维度）"
