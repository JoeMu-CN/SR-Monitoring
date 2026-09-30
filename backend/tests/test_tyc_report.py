"""Todo 6 测试：天眼查多维度风险报告模型与构造器（纯函数，不访问网络/数据库写入）。

fixture 摘自 `tyc_raw_result.md`（宁波鸿腾精密制造股份有限公司实测样例），
超长表格行做受控截断；生成时间固定，保证断言确定、可重复。
"""

import json
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.tyc_report import (
    DimensionFinding,
    DimensionStatus,
    RiskLevel,
    TycRiskReport,
    build_risk_report,
    render_key_summary,
)

SUPPLIER_CODE = "SUP-0001"
GENERATED_AT = datetime(2026, 9, 29, 4, 30, tzinfo=UTC)  # 北京时间 2026-09-29 12:30
ISO_WEEK_KEY = "tyc:SUP-0001:2026-W40"

# Todo 1 final_dimension_set（严格按序）
FINAL_DIMENSION_KEYS = (
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

RAW_RISK_OVERVIEW = "\n".join(
    [
        "# 风险总览：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_risk_overview`",
        "",
        "### 可用专项工具获取详情",
        "",
        "| 风险类型 | 风险等级 | 风险数量 | 详情获取方式 |",
        "|---|---|---|---|",
        "| 开庭公告 | 警示 | 1 | 调用 `get_hearing_notice` 查看详情 |",
        "| 法院公告 | 警示 | 1 | 调用 `get_court_notice` 查看详情 |",
        "| 历史开庭公告 | 警示 | 3 | 调用 `get_historical_hearing_notice` 查看详情 |",
        "",
        "### 需通过风险 ID 获取详情",
        "",
        "| 类型名称 | 风险type | 等级 | title | 风险ID |",
        "|---|---|---|---|---|",
        "| 历史欠税公告 | 79 | 警示 | 该公司曾因拖欠税款而被列入欠税公告名单 | 26742708714 |",
        "",
        "### 周边风险提示",
        "",
        "- 开庭公告 · 警示 · 3 条：投资的宁波市翼腾精密制造有限公司被起诉的开庭公告",
        "- 行政处罚 · 警示 · 1 条：投资的宁波市翼腾精密制造有限公司受到行政处罚",
        "",
    ]
)

RAW_JUDICIAL_CASE = "\n".join(
    [
        "# 风险合规：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_judicial_case`",
        "",
        "### 明细（4 条）",
        "",
        "| # | 案号 | 案件身份 | 案由 | 案件类型 | 审理时间 |",
        "|---|---|---|---|---|---|",
        "| 1 | （2025）苏0583民初35299号 | 被告 | 买卖合同纠纷 | 民事案件 | 2025-12-10 |",
        "| 2 | 浙甬海曙劳人仲案（2019）668号 | 被告 | - | - | 2019-09-18 |",
        "| 3 | （2011）浙甬民一终字第00658号 | 被告 | 劳动合同纠纷 | - | 2011-08-18 |",
        "| 4 | （2011）浙甬民一终字第00210号 | 原告 | 劳动争议 | - | 2011-03-23 |",
        "",
    ]
)

RAW_HEARING_NOTICE = "\n".join(
    [
        "# 开庭公告：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_hearing_notice`",
        "",
        "> 摘要：该查询实体共有1条开庭公告。",
        "",
        "### 明细（1 条）",
        "",
        "| # | 案号 | 法院 | 案由 | 开始日期 |",
        "|---|---|---|---|---|",
        "| 1 | （2025）苏0583民初35299号 | 昆山市人民法院 | 买卖合同纠纷 | 2025-12-10 |",
        "",
    ]
)

RAW_COURT_NOTICE = "\n".join(
    [
        "# 法院公告：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_court_notice`",
        "",
        "> 摘要：该查询实体共有1条法院公告。",
        "",
        "### 明细（1 条）",
        "",
        "| # | 案号 | 法院 | 发布日期 | 内容 | 案由 |",
        "|---|---|---|---|---|---|",
        "| 1 | （2025）苏0583民初35299号 | 昆山市人民法院 | 2025-10-23 | 送达公告 | 买卖合同 |",
        "",
    ]
)

RAW_ADMINISTRATIVE_LICENSE = "\n".join(
    [
        "# 行政许可：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_administrative_license`",
        "",
        "> 摘要：该查询实体共有19条行政许可记录。",
        "",
        "### 行政许可-其他来源",
        "",
        "> 空结果：未发现行政许可-其他来源记录",
        "",
        "### 行政许可-工商局",
        "",
        "| # | 部门 | 决定日期 | 许可名称 | 许可证编号 |",
        "|---|---|---|---|---|",
        "| 1 | 海曙区税务局 | 2021-10-27 | 对纳税人延期缴纳税款的核准 | 330203211027884000758 |",
        "| 2 | 浙江省宁波市人力资源和社会保障局 | 2020-03-20 | 综合计算工时工作制审批 | 无 |",
        "",
    ]
)

RAW_RANDOM_CHECK = "\n".join(
    [
        "# 双随机抽查：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_random_check`",
        "",
        "> 摘要：该查询实体共有3条双随机抽查记录。",
        "",
        "### 明细（3 条）",
        "",
        "| # | checkPlanNum | checkPlanName |",
        "|---|---|---|",
        "| 1 | 甬海市监抽查〔2023〕7号 | 2023年海曙区强制性产品认证获证企业检查 |",
        "| 2 | 甬海市监抽查〔2020〕20号 | 特种设备双随机检查 |",
        "| 3 | 甬海市监抽查〔2021〕54号 | 特种设备使用单位日常监督检查计划 |",
        "",
    ]
)

RAW_SPOT_CHECK = "\n".join(
    [
        "# 抽查检查：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_spot_check_info`",
        "",
        "> 摘要：该查询实体共有1条抽查检查记录。",
        "",
        "### 明细（1 条）",
        "",
        "| # | checkResult | inspectionOrg | productName | sourceType |",
        "|---|---|---|---|---|",
        "| 1 | 合格 | 广东产品质量监督检验研究院 | 固定式通用灯具 | 产品质量监督 |",
        "",
    ]
)

RAW_CREDIT_EVALUATION = "\n".join(
    [
        "# 经营与公示：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_credit_evaluation`",
        "",
        "### 税务评级",
        "",
        "#### 概览字段",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        "| 总数 | 6 |",
        "| 状态 | ok |",
        "",
        "#### 明细（6 条）",
        "",
        "| # | 名称 | 年份 | creditLevel | taxType |",
        "|---|---|---|---|---|",
        "| 1 | 宁波鸿腾精密制造股份有限公司 | 2021 | A | 国税 |",
        "| 2 | 宁波鸿腾精密制造股份有限公司 | 2020 | A | 国税 |",
        "",
        "### 企业信用评级",
        "",
        "> 空结果：未发现企业信用评级记录",
        "",
    ]
)

RAW_SHELL_CHECK = "\n".join(
    [
        "# 风险合规：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_shell_company_check`",
        "",
        "### 概览字段",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        "| 统一社会信用代码 | 913302127995394959 |",
        "| hasShellTags | 0 |",
        "| shellTagsCount | 0 |",
        "",
    ]
)

RAW_CHANGE_RECORDS = "\n".join(
    [
        "# 变更记录：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_change_records`",
        "",
        "> 摘要：该查询实体共有21条变更记录。本次展示20条。",
        "",
        "### 变更记录（20 条）",
        "",
        "| # | 变更事项 | 变更日期 |",
        "|---|---|---|",
        "| 1 | 章程备案 | 2026-01-09 |",
        "| 2 | 名称变更（字号名称、集团名称等） | 2018-10-16 |",
        "| 3 | 地址变更（住所地址、经营场所、驻在地址等变更） | 2017-12-25 |",
        "",
    ]
)

RAW_ACTUAL_CONTROLLER = "\n".join(
    [
        "# 实际控制人：宁波鸿腾精密制造股份有限公司",
        "",
        "- tool: `get_actual_controller`",
        "",
        "> 摘要：该查询实体共有1条实际控制人记录。",
        "",
        "### 实际控制人（1 条）",
        "",
        "| # | 实际控制人 | 比例 | 图谱ID |",
        "|---|---|---|---|",
        "| 1 | 应益军 | 0.559136 | 23460341 |",
        "",
    ]
)

# 非风险维度/非候选工具：即使调用方误传，也不得进入报告（数据最小化）。
DECOY_DIMENSIONS: dict[str, object] = {
    "get_recruitment_info": {
        "status": "success",
        "raw": (
            "# 招聘信息：宁波鸿腾精密制造股份有限公司\n\n"
            "| # | 标题 |\n|---|---|\n| 1 | 仓库主管 |"
        ),
        "message": None,
    },
    "get_competitors": {
        "status": "success",
        "raw": (
            "# 竞品信息：宁波鸿腾精密制造股份有限公司\n\n"
            "| # | 企业名称 |\n|---|---|\n| 1 | 某竞品公司 |"
        ),
        "message": None,
    },
    "search_patents": {
        "status": "success",
        "raw": "# 专利：宁波鸿腾精密制造股份有限公司\n\n> 摘要：共有124条专利记录。",
        "message": None,
    },
}


def _entry(status: str, raw: str | None, message: str | None = None) -> dict[str, object]:
    return {"status": status, "raw": raw, "message": message}


def _fixture_results() -> dict[str, object]:
    """受控 results：维度故意打乱插入顺序，验证输出仍按 Todo 1 固定顺序。"""
    dimensions: dict[str, object] = {
        **DECOY_DIMENSIONS,
        "get_court_notice": _entry("success", RAW_COURT_NOTICE),
        "get_shell_company_check": _entry("success", RAW_SHELL_CHECK),
        "get_risk_overview": _entry("success", RAW_RISK_OVERVIEW),
        "get_actual_controller": _entry("success", RAW_ACTUAL_CONTROLLER),
        "get_hearing_notice": _entry("success", RAW_HEARING_NOTICE),
        "get_default_event_info": _entry("empty", None, "未发现该维度记录"),
        "get_change_records": _entry("success", RAW_CHANGE_RECORDS),
        "get_administrative_license": _entry("success", RAW_ADMINISTRATIVE_LICENSE),
        "get_judicial_case": _entry("success", RAW_JUDICIAL_CASE),
        "get_spot_check_info": _entry("success", RAW_SPOT_CHECK),
        "get_credit_evaluation": _entry("success", RAW_CREDIT_EVALUATION),
        "get_random_check": _entry("success", RAW_RANDOM_CHECK),
    }
    return {
        "status": "success",
        "company_name": "宁波鸿腾精密制造股份有限公司",
        "credit_code": "913302127995394959",
        "reg_status": "存续",
        "dimensions": dimensions,
    }


def _minimal_results(dimensions: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "status": "success",
        "company_name": "测试企业有限公司",
        "credit_code": None,
        "reg_status": None,
        "dimensions": {} if dimensions is None else dimensions,
    }


def _build(
    results: dict[str, object] | None = None,
    *,
    supplier_code: str = SUPPLIER_CODE,
    generated_at: datetime | None = GENERATED_AT,
) -> TycRiskReport:
    return build_risk_report(
        _fixture_results() if results is None else results,
        supplier_code=supplier_code,
        generated_at=generated_at,
    )


def _findings_by_key(report: TycRiskReport) -> dict[str, DimensionFinding]:
    return {finding.key: finding for finding in report.dimensions}


def test_report_covers_final_dimension_set_in_canonical_order() -> None:
    report = _build()

    assert [finding.key for finding in report.dimensions] == list(FINAL_DIMENSION_KEYS)
    assert report.report_kind == "supplier_profile"
    assert report.company_name == "宁波鸿腾精密制造股份有限公司"
    assert report.credit_code == "913302127995394959"
    assert report.reg_status == "存续"


def test_dimension_hits_identify_risk_dimensions_only() -> None:
    report = _build()
    findings = _findings_by_key(report)

    hit_keys = [finding.key for finding in report.dimensions if finding.hit]
    assert hit_keys == [
        "get_risk_overview",
        "get_judicial_case",
        "get_hearing_notice",
        "get_court_notice",
    ]
    assert findings["get_default_event_info"].status is DimensionStatus.EMPTY
    assert findings["get_default_event_info"].hit is False
    assert findings["get_shell_company_check"].hit is False
    assert findings["get_administrative_license"].hit is False
    assert findings["get_administrative_license"].status is DimensionStatus.SUCCESS
    for key in (
        "get_random_check",
        "get_spot_check_info",
        "get_credit_evaluation",
        "get_change_records",
        "get_actual_controller",
    ):
        assert findings[key].status is DimensionStatus.SUCCESS
        assert findings[key].hit is False


def test_risk_level_extracted_from_overview_only() -> None:
    findings = _findings_by_key(_build())

    assert findings["get_risk_overview"].risk_level is RiskLevel.ALERT
    assert findings["get_hearing_notice"].risk_level is None
    assert findings["get_court_notice"].risk_level is None
    assert findings["get_administrative_license"].risk_level is None


def test_key_summary_contains_required_risk_hits() -> None:
    summary = render_key_summary(_build())

    for word in ("重点命中", "历史欠税公告", "开庭公告", "法院公告", "周边风险"):
        assert word in summary
    for word in ("招聘", "竞品", "专利"):
        assert word not in summary


def test_report_excludes_recruitment_competitor_patent_content() -> None:
    report = _build()

    assert len(report.dimensions) == len(FINAL_DIMENSION_KEYS)
    payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    for banned in (
        "招聘",
        "竞品",
        "专利",
        "get_recruitment_info",
        "get_competitors",
        "search_patents",
    ):
        assert banned not in payload


def test_dimension_summaries_are_bounded_and_not_raw_markdown() -> None:
    report = _build()

    for finding in report.dimensions:
        assert len(finding.summary) <= 200
        assert "\n" not in finding.summary
        assert "```" not in finding.summary
        assert "|---" not in finding.summary
        assert len(finding.evidence_refs) <= 5
        if finding.raw_ref is not None:
            assert finding.raw_ref.startswith(f"dimensions.{finding.key}.raw")
            assert "13800138000" not in finding.raw_ref


def test_report_dump_carries_identity_fields_and_json_serializable() -> None:
    report = _build()
    dump = report.model_dump(mode="json")

    assert dump["report_kind"] == "supplier_profile"
    assert dump["period_key"] == ISO_WEEK_KEY
    assert dump["supplier_code"] == SUPPLIER_CODE
    assert isinstance(dump["generated_at"], str)
    assert datetime.fromisoformat(dump["generated_at"]) == GENERATED_AT
    assert dump["dimensions"][0]["status"] == "success"
    assert dump["dimensions"][0]["key"] == "get_risk_overview"
    assert all(isinstance(item["evidence_refs"], list) for item in dump["dimensions"])
    # 不抛异常即证明可 JSON 序列化
    json.dumps(dump, ensure_ascii=False)


@pytest.mark.parametrize(
    ("generated_at", "expected_week"),
    [
        (datetime(2025, 12, 28, 12, 0, tzinfo=timezone(timedelta(hours=8))), "2025-W52"),
        (datetime(2025, 12, 29, 0, 0, tzinfo=timezone(timedelta(hours=8))), "2026-W01"),
        (datetime(2024, 12, 30, 8, 0, tzinfo=timezone(timedelta(hours=8))), "2025-W01"),
        (datetime(2025, 12, 28, 20, 0, tzinfo=UTC), "2026-W01"),
    ],
)
def test_period_key_uses_iso_week_with_beijing_boundary(
    generated_at: datetime, expected_week: str
) -> None:
    report = _build(_minimal_results(), generated_at=generated_at)

    assert report.period_key == f"tyc:{SUPPLIER_CODE}:{expected_week}"


def test_period_key_same_instant_regardless_of_offset() -> None:
    utc_moment = datetime(2025, 12, 28, 20, 0, tzinfo=UTC)
    beijing_moment = utc_moment.astimezone(timezone(timedelta(hours=8)))
    assert beijing_moment.hour == 4  # 同一瞬时，仅时区表达不同

    first = _build(_minimal_results(), generated_at=utc_moment)
    second = _build(_minimal_results(), generated_at=beijing_moment)

    assert first.period_key == second.period_key == f"tyc:{SUPPLIER_CODE}:2026-W01"


def test_generated_at_defaults_to_aware_utc() -> None:
    before = datetime.now(UTC)
    report = _build(_minimal_results(), generated_at=None)
    after = datetime.now(UTC)

    assert report.generated_at.tzinfo is not None
    assert before <= report.generated_at <= after


def test_naive_generated_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _build(_minimal_results(), generated_at=datetime(2026, 9, 29, 4, 30))


@pytest.mark.parametrize("supplier_code", ["", "   "])
def test_blank_supplier_code_rejected(supplier_code: str) -> None:
    with pytest.raises(ValidationError):
        _build(_minimal_results(), supplier_code=supplier_code)


def test_dimension_status_enum_matches_input_contract() -> None:
    assert {status.value for status in DimensionStatus} == {
        "success",
        "empty",
        "error",
        "quota_exhausted",
        "busy",
    }


def test_unknown_dimension_status_rejected() -> None:
    results = _minimal_results(
        {"get_risk_overview": _entry("weird_status", "任一内容")}
    )
    with pytest.raises(ValueError):
        _build(results)


def test_dimension_entry_must_be_mapping() -> None:
    results = _minimal_results({"get_risk_overview": "not-a-mapping"})
    with pytest.raises(ValueError):
        _build(results)


@pytest.mark.parametrize(
    "raw",
    [None, "   ", "> 空结果：未发现该维度记录"],
)
def test_blank_or_empty_success_normalized_to_empty(raw: str | None) -> None:
    results = _minimal_results({"get_default_event_info": _entry("success", raw)})
    report = _build(results)

    finding = report.dimensions[0]
    assert finding.status is DimensionStatus.EMPTY
    assert finding.hit is False
    assert "未发现" in finding.summary
    assert finding.raw_ref is None
    assert finding.evidence_refs == []


@pytest.mark.parametrize("status", ["error", "quota_exhausted", "busy"])
def test_non_success_statuses_preserved_without_hits(status: str) -> None:
    results = _minimal_results(
        {"get_hearing_notice": _entry(status, None, "测试失败详情")}
    )
    report = _build(results)

    finding = report.dimensions[0]
    assert finding.status.value == status
    assert finding.hit is False
    assert finding.risk_level is None
    assert finding.raw_ref is None
    assert finding.evidence_refs == []
    if status == "error":
        assert "查询失败" in finding.summary
    if status == "quota_exhausted":
        assert "额度" in finding.summary
    if status == "busy":
        assert "繁忙" in finding.summary


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DimensionFinding(
            key="k",
            name="n",
            status="success",
            hit=False,
            summary="s",
            unexpected_field="x",
        )
    with pytest.raises(ValidationError):
        TycRiskReport(
            company_name="x",
            supplier_code="c",
            generated_at=GENERATED_AT,
            period_key="tyc:c:2026-W40",
            dimensions=[],
            unexpected_field="x",
        )


def test_oversized_raw_is_capped() -> None:
    row = "| 开庭公告 | 警示 | 1 | 详情 |"
    raw = "\n".join(
        ["| 风险类型 | 风险等级 | 风险数量 | 详情 |", "|---|---|---|---|"] + [row] * 5000
    )
    results = _minimal_results({"get_risk_overview": _entry("success", raw)})

    report = _build(results)
    finding = report.dimensions[0]

    assert finding.hit is True
    assert len(finding.summary) <= 200
    assert len(finding.evidence_refs) <= 5
    assert len(json.dumps(report.model_dump(mode="json"), ensure_ascii=False)) < 20000


def test_sensitive_numbers_masked_in_extracted_text() -> None:
    raw = "\n".join(
        [
            "# 开庭公告：测试企业",
            "",
            "> 摘要：该查询实体共有1条开庭公告。",
            "",
            "### 联系方式",
            "",
            "| # | 联系电话 |",
            "|---|---|",
            "| 1 | 13800138000 |",
            "| 2 | 0574-89110806 |",
            "",
        ]
    )
    results = _minimal_results({"get_hearing_notice": _entry("success", raw)})

    report = _build(results)
    payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)

    assert "13800138000" not in payload
    assert "0574-89110806" not in payload
    assert "[已脱敏]" in payload


def test_bullet_evidence_masks_sensitive_and_bounds_length() -> None:
    """无分隔符的 bullet 抽取证据同样脱敏且有界，不随原始长度放大。"""
    phone = "13800138000"
    id_number = "110101199001011234"
    huge = "Z" * 5000
    raw = "\n".join(
        [
            "# 开庭公告：测试企业有限公司",
            "",
            "- tool: `get_hearing_notice`",
            "",
            "> 摘要：命中开庭公告",
            "",
            "### 开庭公告",
            "",
            f"- 联系电话 {phone}",
            f"- 证件号 {id_number}",
            f"- 备注 {huge}",
            "",
        ]
    )
    results = _minimal_results({"get_hearing_notice": _entry("success", raw)})

    report = _build(results)
    payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    finding = report.dimensions[0]

    assert finding.hit is True
    assert phone not in payload
    assert id_number not in payload
    assert "[已脱敏]" in payload
    assert len(finding.evidence_refs) <= 5
    assert all(len(item) <= 41 for item in finding.evidence_refs)


def test_manual_qa_temp_json_roundtrip() -> None:
    """Manual QA：落临时 JSON、回读模型、二次序列化一致，并复查最小化约束。"""
    report = _build()
    dump = report.model_dump(mode="json")

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "tyc-report.json"
        path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
        text = path.read_text(encoding="utf-8")

        assert "```" not in text
        assert "| 风险类型 |" not in text
        for banned in ("招聘", "竞品", "专利", "13800138000", "0574-89110806"):
            assert banned not in text

        reloaded = TycRiskReport.model_validate(json.loads(text))
        assert reloaded.model_dump(mode="json") == dump

    assert not path.exists()
