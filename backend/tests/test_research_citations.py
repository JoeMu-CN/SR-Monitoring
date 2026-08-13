"""研究来源快照和引用回验测试。"""

import pytest

from app.research.citations import (
    CitationValidationError,
    build_research_citation,
    build_research_source,
    excerpt_hash,
    normalize_research_text,
    verify_quote,
)
from app.research.web import ResearchPageRead


def page_read() -> ResearchPageRead:
    return ResearchPageRead(
        requested_url="https://official.example/start",
        final_url="https://official.example/final",
        redirect_chain=("https://official.example/final",),
        status_code=200,
        content_type="text/html",
        excerpt="官方公告  供应链信息正常",
    )


def test_normalize_text_and_excerpt_hash_are_stable() -> None:
    assert normalize_research_text("  官方公告\n供应链信息正常 ") == "官方公告 供应链信息正常"
    assert excerpt_hash("官方公告  供应链信息正常") == excerpt_hash("官方公告 供应链信息正常")
    assert excerpt_hash("官方公告  供应链信息正常") != excerpt_hash("官方公告  供应链信息异常")


def test_build_source_keeps_final_url_and_records_hash_scope() -> None:
    source = build_research_source(
        7,
        page_read(),
        title="  官方公告\n供应链信息 ",
        metadata={"provider": "fake"},
    )

    assert source.task_id == 7
    assert source.url == "https://official.example/final"
    assert source.title == "官方公告 供应链信息"
    assert source.content_hash == excerpt_hash(source.content_excerpt or "")
    assert source.source_metadata["hash_scope"] == "normalized_excerpt"
    assert source.source_metadata["requested_url"] == "https://official.example/start"
    assert source.source_metadata["provider_metadata"] == {"provider": "fake"}


def test_verify_quote_accepts_normalized_whitespace_and_returns_offsets() -> None:
    verification = verify_quote("官方公告 供应链信息", "官方公告\n供应链信息正常")

    assert verification.verified is True
    assert verification.quote == "官方公告 供应链信息"
    assert verification.start == 0
    assert verification.end == len("官方公告 供应链信息")


def test_unverified_quote_is_retained_as_unverified_citation() -> None:
    citation = build_research_citation(
        7,
        11,
        quote="不存在的结论",
        excerpt=page_read().excerpt,
        locator="正文第 1 段",
    )

    assert citation.quote == "不存在的结论"
    assert citation.verified is False
    assert citation.locator == "正文第 1 段"


@pytest.mark.parametrize(
    "builder_args",
    [
        {"task_id": 0, "source_id": 1},
        {"task_id": 1, "source_id": 0},
    ],
)
def test_citation_rejects_non_positive_ids(builder_args: dict[str, int]) -> None:
    with pytest.raises(CitationValidationError):
        build_research_citation(
            builder_args["task_id"],
            builder_args["source_id"],
            quote="引用",
            excerpt="引用正文",
        )


def test_quote_rejects_empty_or_oversized_content() -> None:
    with pytest.raises(CitationValidationError):
        verify_quote("   ", "正文")
    with pytest.raises(CitationValidationError):
        verify_quote("x" * 2001, "正文")
