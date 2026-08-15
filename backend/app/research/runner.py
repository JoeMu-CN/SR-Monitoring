"""本地研究 Worker 的受控常驻入口。

支持本地生命周期、显式 URL 单页读取和按主题自动发现信源三种受控模式。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
from collections.abc import Callable
from threading import Event

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import AIProvider, get_ai_provider
from app.auth.security import write_audit
from app.config import (
    RESEARCH_WORKER_ENABLED,
    RESEARCH_WORKER_LEASE_SECONDS,
    RESEARCH_WORKER_MODE,
    RESEARCH_WORKER_POLL_SECONDS,
    get_ai_settings,
)
from app.database import SessionLocal
from app.research.citations import build_research_citation, build_research_source
from app.research.models import ResearchCitation, ResearchSource, ResearchTask
from app.research.reporting import (
    ResearchEvidenceInput,
    ResearchReportGenerationInput,
    canonicalize_generated_report,
)
from app.research.search import (
    MAX_RESULTS_PER_QUERY,
    SearchBudget,
    SearchProvider,
    build_configured_search_provider,
    run_search,
)
from app.research.service import (
    ResearchBudgetExceeded,
    create_generated_report,
    record_task_usage,
    reserve_task_usage,
)
from app.research.web import read_public_page
from app.research.worker import run_once

LOGGER = logging.getLogger(__name__)
LOCAL_LIFECYCLE_TEST_MODE = "local_lifecycle_test"
LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX = "[local-worker-test]"
CONTROLLED_SINGLE_PAGE_MODE = "controlled_single_page"
TOPIC_SOURCE_DISCOVERY_MODE = "topic_source_discovery"
MAX_REPORT_EVIDENCE_CHARS = 400


def execute_local_lifecycle_test(session: Session, task: ResearchTask) -> None:
    """完成无外部访问的生命周期测试任务。"""
    if task.task_type != "manual" or not task.topic.startswith(LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX):
        raise ValueError("本地 Worker 只允许执行显式标记的手动测试任务")
    updated = record_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        current_step="local_lifecycle_test_completed",
    )
    if updated is None:
        raise RuntimeError("本地生命周期测试任务租约无效")


def run_local_lifecycle_test_once(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
) -> ResearchTask | None:
    """只认领显式本地测试任务，普通研究任务保持 queued。"""
    return run_once(
        session,
        worker_id=worker_id,
        execute=execute_local_lifecycle_test,
        lease_seconds=lease_seconds,
        task_type="manual",
        topic_prefix=LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX,
    )


def execute_controlled_single_pages(session: Session, task: ResearchTask) -> None:
    """读取任务显式允许的公开 HTTPS 单页，并保存来源和可回验引用。

    外部请求只会由 ``read_public_page`` 发出；它已执行公网 HTTPS、DNS、重定向、
    响应大小、域名租约与冷却控制。本执行器不搜索、不执行 JavaScript，也不生成模型报告。
    """
    source_urls = list(task.source_urls)
    max_pages = _research_max_pages(task)
    if not source_urls:
        raise ValueError("受控单页任务缺少来源 URL")
    if len(source_urls) > max_pages:
        raise ResearchBudgetExceeded("研究任务超过预算：max_pages")

    for index, url in enumerate(source_urls, start=1):
        session.refresh(task)
        if task.cancel_requested_at is not None:
            return
        reserved = reserve_task_usage(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            search_results_delta=1,
            current_step=f"controlled_page_reading:{index}/{len(source_urls)}",
        )
        if reserved is None:
            raise RuntimeError("受控单页任务租约无效")
        page = asyncio.run(read_public_page(url))
        source = build_research_source(task.id, page, source_type="controlled_web")
        session.add(source)
        session.flush()
        if source.content_excerpt:
            session.add(
                build_research_citation(
                    task.id,
                    source.id,
                    quote=source.content_excerpt[:200],
                    excerpt=source.content_excerpt,
                    locator="正文摘要",
                )
            )
        session.commit()
        updated = record_task_usage(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            current_step=f"controlled_page_read_complete:{index}/{len(source_urls)}",
        )
        if updated is None:
            raise RuntimeError("受控单页任务租约无效")


def _research_max_pages(task: ResearchTask) -> int:
    raw_limit = task.budget_snapshot.get("max_pages")
    try:
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool):
            limit = raw_limit
        elif isinstance(raw_limit, str):
            limit = int(raw_limit)
        else:
            raise ValueError
    except ValueError as exc:
        raise ResearchBudgetExceeded("预算配置无效：max_pages") from exc
    if limit < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_pages")
    return limit


def run_controlled_single_page_once(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
) -> ResearchTask | None:
    """只认领显式给出来源 URL 的手动任务。"""
    return run_once(
        session,
        worker_id=worker_id,
        execute=execute_controlled_single_pages,
        lease_seconds=lease_seconds,
        task_type="manual",
        require_source_urls=True,
        require_execution_requested=True,
    )


def execute_topic_source_discovery(
    session: Session,
    task: ResearchTask,
    *,
    provider: SearchProvider | None = None,
    report_provider: AIProvider | None = None,
) -> None:
    """按任务主题搜索候选来源，并通过受控单页读取器保存证据。"""
    search_provider = provider or build_configured_search_provider()
    if search_provider is None:
        raise RuntimeError("研究搜索 Provider 未配置")

    remaining_results = _research_budget_limit(task, "max_results") - task.search_results_used
    requested_results = min(
        MAX_RESULTS_PER_QUERY,
        _research_max_pages(task),
        remaining_results,
    )
    if requested_results < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_results")

    reserved = reserve_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        search_queries_delta=1,
        current_step="searching_public_sources",
    )
    if reserved is None:
        raise RuntimeError("自动信源发现任务租约无效")

    response = asyncio.run(
        run_search(
            search_provider,
            task.topic,
            budget=SearchBudget(max_queries=1, max_results=requested_results),
            max_results=requested_results,
        )
    )
    if not response.results:
        raise RuntimeError("搜索未返回可用公开来源")

    saved_count = 0
    for index, candidate in enumerate(response.results, start=1):
        session.refresh(task)
        if task.cancel_requested_at is not None:
            return
        reserved = reserve_task_usage(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            search_results_delta=1,
            current_step=f"reading_discovered_source:{index}/{len(response.results)}",
        )
        if reserved is None:
            raise RuntimeError("自动信源发现任务租约无效")
        try:
            page = asyncio.run(read_public_page(candidate.url))
        except (ValueError, RuntimeError):
            LOGGER.warning("自动发现候选来源读取失败：task_id=%s index=%s", task.id, index)
            continue
        source = build_research_source(
            task.id,
            page,
            title=candidate.title,
            source_type="web_search",
            metadata={
                "provider": response.provider_name,
                "query": response.query,
                "snippet": candidate.snippet,
                "published_at": candidate.published_at,
            },
        )
        session.add(source)
        session.flush()
        if source.content_excerpt:
            session.add(
                build_research_citation(
                    task.id,
                    source.id,
                    quote=source.content_excerpt[:200],
                    excerpt=source.content_excerpt,
                    locator="正文摘要",
                )
            )
        session.commit()
        saved_count += 1

    if saved_count == 0:
        raise RuntimeError("搜索候选来源均无法受控读取")
    _generate_report_draft(
        session,
        task,
        provider=report_provider or get_ai_provider(get_ai_settings()),
    )
    updated = record_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        current_step=f"report_draft_created:{saved_count}/{len(response.results)}",
    )
    if updated is None:
        raise RuntimeError("自动信源发现任务租约无效")


def _generate_report_draft(
    session: Session,
    task: ResearchTask,
    *,
    provider: AIProvider,
) -> None:
    context = _report_generation_input(session, task)
    remaining_input = _research_budget_limit(task, "max_input_tokens") - task.input_tokens_used
    remaining_output = (
        _research_budget_limit(task, "max_output_tokens") - task.output_tokens_used
    )
    if remaining_input < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_input_tokens")
    if remaining_output < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_output_tokens")

    generated = asyncio.run(
        provider.generate_research_report(context, max_output_tokens=remaining_output)
    )
    draft = canonicalize_generated_report(generated.draft, context)
    updated = record_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        input_tokens_delta=generated.input_tokens,
        output_tokens_delta=generated.output_tokens,
        current_step="research_report_generated",
    )
    if updated is None:
        raise RuntimeError("研究报告任务租约无效")
    report = create_generated_report(
        session,
        task_id=task.id,
        draft=draft,
        model_version=provider.model,
    )
    write_audit(
        session,
        action="research_report_draft_generated",
        resource_type="research_report",
        resource_id=str(report.id),
        detail=f"task_id={task.id};provider={provider.provider_name};model={provider.model}",
    )
    session.commit()


def _report_generation_input(
    session: Session, task: ResearchTask
) -> ResearchReportGenerationInput:
    sources = list(
        session.scalars(
            select(ResearchSource)
            .where(ResearchSource.task_id == task.id)
            .order_by(ResearchSource.id)
            .limit(20)
        )
    )
    source_by_id = {source.id: source for source in sources}
    citations = list(
        session.scalars(
            select(ResearchCitation)
            .where(
                ResearchCitation.task_id == task.id,
                ResearchCitation.verified.is_(True),
                ResearchCitation.source_id.in_(source_by_id),
            )
            .order_by(ResearchCitation.id)
        )
    )
    evidence = [
        ResearchEvidenceInput(
            citation_id=f"citation-{citation.id}",
            title=source_by_id[citation.source_id].title,
            url=source_by_id[citation.source_id].url,
            quote=citation.quote,
            excerpt=(source_by_id[citation.source_id].content_excerpt or "")[
                :MAX_REPORT_EVIDENCE_CHARS
            ],
        )
        for citation in citations
        if source_by_id[citation.source_id].content_excerpt
    ]
    if not evidence:
        raise RuntimeError("没有可用于研究报告的已回验来源")
    return ResearchReportGenerationInput(topic=task.topic, evidence=evidence)


def _research_budget_limit(task: ResearchTask, key: str) -> int:
    raw_limit = task.budget_snapshot.get(key)
    try:
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool):
            limit = raw_limit
        elif isinstance(raw_limit, str):
            limit = int(raw_limit)
        else:
            raise ValueError
    except ValueError as exc:
        raise ResearchBudgetExceeded(f"预算配置无效：{key}") from exc
    if limit < 1:
        raise ResearchBudgetExceeded(f"研究任务超过预算：{key}")
    return limit


def run_topic_source_discovery_once(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    report_provider: AIProvider | None = None,
) -> ResearchTask | None:
    """只认领已显式开始的手动研究任务并自动发现来源。"""
    return run_once(
        session,
        worker_id=worker_id,
        execute=lambda current_session, task: execute_topic_source_discovery(
            current_session,
            task,
            report_provider=report_provider,
        ),
        lease_seconds=lease_seconds,
        task_type="manual",
        require_execution_requested=True,
    )


def run_forever(
    *,
    worker_id: str,
    lease_seconds: int,
    poll_seconds: float,
    stop_event: Event,
    run_task_once: Callable[[Session, str, int], ResearchTask | None],
    session_factory: Callable[[], Session] = SessionLocal,
) -> None:
    """轮询本地测试队列；停止信号到达后不再认领新任务。"""
    while not stop_event.is_set():
        with session_factory() as session:
            completed = run_task_once(session, worker_id, lease_seconds)
        if completed is None:
            stop_event.wait(poll_seconds)


def main() -> None:
    """运行由 Compose profile 显式启动的本地测试 Worker。"""
    if not RESEARCH_WORKER_ENABLED:
        LOGGER.warning("research-worker 未启用，退出且不会认领研究任务")
        return
    def _run_local_test(
        session: Session, worker_id: str, lease_seconds: int
    ) -> ResearchTask | None:
        return run_local_lifecycle_test_once(
            session, worker_id=worker_id, lease_seconds=lease_seconds
        )

    def _run_controlled_page(
        session: Session, worker_id: str, lease_seconds: int
    ) -> ResearchTask | None:
        return run_controlled_single_page_once(
            session, worker_id=worker_id, lease_seconds=lease_seconds
        )

    def _run_topic_source_discovery(
        session: Session, worker_id: str, lease_seconds: int
    ) -> ResearchTask | None:
        return run_topic_source_discovery_once(
            session, worker_id=worker_id, lease_seconds=lease_seconds
        )

    runners: dict[str, Callable[[Session, str, int], ResearchTask | None]] = {
        LOCAL_LIFECYCLE_TEST_MODE: _run_local_test,
        CONTROLLED_SINGLE_PAGE_MODE: _run_controlled_page,
        TOPIC_SOURCE_DISCOVERY_MODE: _run_topic_source_discovery,
    }
    run_task_once = runners.get(RESEARCH_WORKER_MODE)
    if run_task_once is None:
        raise RuntimeError(
            "当前仅支持 local_lifecycle_test、controlled_single_page "
            "或 topic_source_discovery 模式"
        )

    stop_event = Event()

    def _request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    worker_id = f"local-research-worker:{socket.gethostname()}"
    LOGGER.info("research-worker 已启动：mode=%s worker_id=%s", RESEARCH_WORKER_MODE, worker_id)
    run_forever(
        worker_id=worker_id,
        lease_seconds=RESEARCH_WORKER_LEASE_SECONDS,
        poll_seconds=RESEARCH_WORKER_POLL_SECONDS,
        stop_event=stop_event,
        run_task_once=run_task_once,
    )


if __name__ == "__main__":
    main()
