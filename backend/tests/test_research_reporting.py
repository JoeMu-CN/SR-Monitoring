"""研究报告结构化契约和人工确认闸门测试。"""

import pytest

from app.research.reporting import (
    ReportValidationError,
    ResearchCitationDraft,
    ResearchClaimDraft,
    ResearchReportDraft,
    can_promote_claim,
    validate_report_draft,
)


def valid_report() -> ResearchReportDraft:
    return ResearchReportDraft(
        title="供应链公开信息研究",
        disclaimer="AI 生成，仅供参考，不作为重大决策核心依据。",
        facts=[
            ResearchClaimDraft(
                claim_id="fact-1",
                claim_type="fact",
                text="官方公告披露了供应链调整信息。",
                citation_ids=["c-1"],
                confidence=90,
            )
        ],
        inferences=[
            ResearchClaimDraft(
                claim_id="inference-1",
                claim_type="inference",
                text="该调整可能增加短期交付不确定性。",
                citation_ids=["c-1"],
                confidence=60,
            )
        ],
        forecasts=[
            ResearchClaimDraft(
                claim_id="forecast-1",
                claim_type="forecast",
                text="未来一周仍需关注后续公告。",
                citation_ids=["c-1"],
                confidence=50,
            )
        ],
        citations=[
            ResearchCitationDraft(
                citation_id="c-1",
                url="https://official.example/final",
                quote="官方公告披露了供应链调整信息",
                verified=True,
            )
        ],
    )


def test_valid_report_passes_structured_validation() -> None:
    validate_report_draft(valid_report())


def test_report_requires_disclaimer() -> None:
    report = valid_report().model_copy(update={"disclaimer": "内部草稿"})
    with pytest.raises(ReportValidationError, match="免责声明"):
        validate_report_draft(report)


def test_report_rejects_unverified_citation() -> None:
    report = valid_report().model_copy(
        update={
            "citations": [
                ResearchCitationDraft(
                    citation_id="c-1",
                    url="https://official.example/final",
                    quote="官方公告",
                    verified=False,
                )
            ]
        }
    )
    with pytest.raises(ReportValidationError, match="没有经过回验"):
        validate_report_draft(report)


def test_report_rejects_missing_citation_and_section_mismatch() -> None:
    missing = valid_report().model_copy(
        update={
            "facts": [
                valid_report().facts[0].model_copy(update={"citation_ids": ["missing"]})
            ]
        }
    )
    with pytest.raises(ReportValidationError, match="不存在的引用"):
        validate_report_draft(missing)

    mismatch = valid_report().model_copy(
        update={
            "facts": [
                valid_report().facts[0].model_copy(update={"claim_type": "forecast"})
            ]
        }
    )
    with pytest.raises(ReportValidationError, match="分段不一致"):
        validate_report_draft(mismatch)


def test_report_rejects_duplicate_ids_and_unsafe_url() -> None:
    duplicate = valid_report().model_copy(
        update={
            "citations": [
                valid_report().citations[0],
                valid_report().citations[0],
            ]
        }
    )
    with pytest.raises(ReportValidationError, match="引用 ID 重复"):
        validate_report_draft(duplicate)

    unsafe = valid_report().model_copy(
        update={
            "citations": [
                valid_report().citations[0].model_copy(update={"url": "http://official.example"})
            ]
        }
    )
    with pytest.raises(ReportValidationError, match="HTTPS"):
        validate_report_draft(unsafe)


def test_promotion_requires_manual_approval_and_never_writes_signal() -> None:
    report = valid_report()
    denied = can_promote_claim(report, "fact-1", manually_approved=False)
    allowed = can_promote_claim(report, "fact-1", manually_approved=True)
    missing = can_promote_claim(report, "not-found", manually_approved=True)

    assert denied.allowed is False
    assert "人工确认" in denied.reason
    assert allowed.allowed is True
    assert missing.allowed is False
