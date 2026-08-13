"""研究报告草稿的结构化契约与人工确认闸门。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

ReportSection = Literal["fact", "inference", "forecast"]
REQUIRED_DISCLAIMER = "AI 生成，仅供参考"


class ReportValidationError(ValueError):
    """报告草稿不满足引用或人工确认边界。"""


class ResearchCitationDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    citation_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2000)
    quote: str = Field(min_length=1, max_length=2000)
    verified: bool = False


class ResearchClaimDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str = Field(min_length=1, max_length=128)
    claim_type: ReportSection
    text: str = Field(min_length=1, max_length=4000)
    citation_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: int | None = Field(default=None, ge=0, le=100)


class ResearchReportDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=500)
    disclaimer: str = Field(min_length=1, max_length=500)
    facts: list[ResearchClaimDraft] = Field(default_factory=list, max_length=100)
    inferences: list[ResearchClaimDraft] = Field(default_factory=list, max_length=100)
    forecasts: list[ResearchClaimDraft] = Field(default_factory=list, max_length=100)
    citations: list[ResearchCitationDraft] = Field(default_factory=list, max_length=500)


@dataclass(frozen=True)
class PromotionDecision:
    allowed: bool
    reason: str


def validate_report_draft(report: ResearchReportDraft) -> None:
    """校验报告可否进入人工阅读，不代表报告内容真实。"""
    if REQUIRED_DISCLAIMER not in report.disclaimer:
        raise ReportValidationError("报告必须包含“AI 生成，仅供参考”免责声明")

    citations: dict[str, ResearchCitationDraft] = {}
    for citation in report.citations:
        if citation.citation_id in citations:
            raise ReportValidationError(f"引用 ID 重复：{citation.citation_id}")
        _validate_public_https_url(citation.url)
        citations[citation.citation_id] = citation

    claims: dict[str, ResearchClaimDraft] = {}
    sections: tuple[tuple[ReportSection, list[ResearchClaimDraft]], ...] = (
        ("fact", report.facts),
        ("inference", report.inferences),
        ("forecast", report.forecasts),
    )
    for section, section_claims in sections:
        for claim in section_claims:
            if claim.claim_type != section:
                raise ReportValidationError(
                    f"结论 {claim.claim_id} 的类型与报告分段不一致"
                )
            if claim.claim_id in claims:
                raise ReportValidationError(f"结论 ID 重复：{claim.claim_id}")
            claims[claim.claim_id] = claim
            missing = set(claim.citation_ids) - citations.keys()
            if missing:
                raise ReportValidationError(
                    f"结论 {claim.claim_id} 引用了不存在的引用：{sorted(missing)}"
                )
            if not any(citations[citation_id].verified for citation_id in claim.citation_ids):
                raise ReportValidationError(
                    f"结论 {claim.claim_id} 没有经过回验的引用"
                )


def can_promote_claim(
    report: ResearchReportDraft,
    claim_id: str,
    *,
    manually_approved: bool,
) -> PromotionDecision:
    """只返回人工确认闸门结果，不执行任何风险信号写入。"""
    validate_report_draft(report)
    all_claims = [*report.facts, *report.inferences, *report.forecasts]
    claim = next((item for item in all_claims if item.claim_id == claim_id), None)
    if claim is None:
        return PromotionDecision(False, "结论不存在")
    if not manually_approved:
        return PromotionDecision(False, "尚未完成人工确认")
    return PromotionDecision(True, "已通过结构化引用校验和人工确认闸门")


def _validate_public_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ReportValidationError("报告引用仅允许绝对 HTTPS URL")
    if parsed.username or parsed.password:
        raise ReportValidationError("报告引用 URL 不允许包含用户凭据")
