"""研究来源快照和引用回验的确定性基础。"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

from app.research.models import ResearchCitation, ResearchSource
from app.research.web import ResearchPageRead

MAX_CITATION_QUOTE_CHARS = 2_000
MAX_SOURCE_TITLE_CHARS = 500


class CitationValidationError(ValueError):
    """来源或引用字段不符合研究轨边界。"""


@dataclass(frozen=True)
class CitationVerification:
    quote: str
    verified: bool
    start: int | None
    end: int | None


def normalize_research_text(value: str) -> str:
    """统一 Unicode 和空白；不删除正文中的标点或语义字符。"""
    return " ".join(unicodedata.normalize("NFKC", value).split())


def excerpt_hash(excerpt: str) -> str:
    normalized = normalize_research_text(excerpt)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_quote(quote: str, excerpt: str) -> CitationVerification:
    normalized_quote = normalize_research_text(quote)
    normalized_excerpt = normalize_research_text(excerpt)
    if not normalized_quote:
        raise CitationValidationError("引用片段不能为空")
    if len(normalized_quote) > MAX_CITATION_QUOTE_CHARS:
        raise CitationValidationError(
            f"引用片段不能超过 {MAX_CITATION_QUOTE_CHARS} 个字符"
        )
    start = normalized_excerpt.find(normalized_quote)
    if start < 0:
        return CitationVerification(normalized_quote, False, None, None)
    return CitationVerification(
        normalized_quote,
        True,
        start,
        start + len(normalized_quote),
    )


def build_research_source(
    task_id: int,
    page: ResearchPageRead,
    *,
    title: str | None = None,
    source_type: str = "web",
    credibility_tier: str = "unrated",
    metadata: dict[str, object] | None = None,
) -> ResearchSource:
    """把单页读取结果转换为待持久化的来源快照。"""
    if task_id < 1:
        raise CitationValidationError("task_id 必须为正整数")
    if not source_type.strip() or not credibility_tier.strip():
        raise CitationValidationError("来源类型和可信度分级不能为空")
    excerpt = normalize_research_text(page.excerpt)
    source_metadata: dict[str, object] = {
        "requested_url": page.requested_url,
        "final_url": page.final_url,
        "redirect_chain": list(page.redirect_chain),
        "content_type": page.content_type,
        "reader": page.reader,
        "hash_scope": "normalized_excerpt",
    }
    if metadata:
        source_metadata["provider_metadata"] = dict(metadata)
    return ResearchSource(
        task_id=task_id,
        url=page.final_url,
        title=normalize_research_text(title or "")[:MAX_SOURCE_TITLE_CHARS] or None,
        source_type=source_type.strip(),
        credibility_tier=credibility_tier.strip(),
        http_status=page.status_code,
        content_hash=excerpt_hash(excerpt),
        content_excerpt=excerpt,
        source_metadata=source_metadata,
    )


def build_research_citation(
    task_id: int,
    source_id: int,
    *,
    quote: str,
    excerpt: str,
    locator: str | None = None,
) -> ResearchCitation:
    """构造引用记录；未命中正文时保留记录但标记为未验证。"""
    if task_id < 1 or source_id < 1:
        raise CitationValidationError("task_id 和 source_id 必须为正整数")
    verification = verify_quote(quote, excerpt)
    return ResearchCitation(
        task_id=task_id,
        source_id=source_id,
        quote=verification.quote,
        locator=normalize_research_text(locator or "") or None,
        verified=verification.verified,
    )
