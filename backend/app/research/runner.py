"""本地研究 Worker 的受控常驻入口。

支持本地生命周期、显式 URL 单页读取和按主题自动发现信源三种受控模式。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Thread
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.models import AIAnalysisRecord
from app.ai.providers import AIProvider, get_ai_provider
from app.ai.schemas import LocationReference
from app.auth.security import write_audit
from app.config import (
    DATABASE_URL,
    RESEARCH_ORCHESTRATOR,
    RESEARCH_WORKER_ENABLED,
    RESEARCH_WORKER_HEARTBEAT_INTERVAL_SECONDS,
    RESEARCH_WORKER_LEASE_SECONDS,
    RESEARCH_WORKER_MODE,
    RESEARCH_WORKER_POLL_SECONDS,
    get_ai_settings,
    get_search_settings,
)
from app.database import SessionLocal
from app.research.citations import (
    build_research_citation,
    build_research_source,
    excerpt_hash,
    normalize_research_text,
)
from app.research.graph import (
    MIN_VERIFIED_EVIDENCE,
    RESEARCH_GRAPH_VERSION,
    EvidenceOutcome,
    ResearchGraphState,
    build_topic_source_discovery_graph,
)
from app.research.models import ResearchCitation, ResearchReport, ResearchSource, ResearchTask
from app.research.reporting import (
    REQUIRED_DISCLAIMER,
    ReportValidationError,
    ResearchEvidenceInput,
    ResearchReportDraft,
    ResearchReportGenerationInput,
    canonicalize_generated_report,
)
from app.research.search import (
    MAX_RESULTS_PER_QUERY,
    SearchBudget,
    SearchCandidate,
    SearchProvider,
    SearchResponse,
    build_configured_search_provider,
    run_search,
)
from app.research.service import (
    ResearchBudgetExceeded,
    append_task_event,
    claim_tool_run,
    complete_tool_run,
    create_generated_report,
    fail_tool_run,
    record_task_usage,
    reserve_provider_quota,
    reserve_task_usage,
    stop_worker_heartbeat,
    touch_worker_heartbeat,
    upsert_worker_heartbeat,
)
from app.research.web import read_public_page
from app.research.worker import run_once
from app.risks.engine.matching import matches_location_reference
from app.risks.models import (
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.signals.request_control import SourceRequestFailed
from app.signals.sources import SourceFetchError
from app.suppliers.models import Supplier

LOGGER = logging.getLogger(__name__)
LOCAL_LIFECYCLE_TEST_MODE = "local_lifecycle_test"
LOCAL_LIFECYCLE_TEST_TOPIC_PREFIX = "[local-worker-test]"
CONTROLLED_SINGLE_PAGE_MODE = "controlled_single_page"
TOPIC_SOURCE_DISCOVERY_MODE = "topic_source_discovery"
MAX_REPORT_EVIDENCE_CHARS = 400
MAX_MONITORING_CONTEXT_ITEMS = 5
MAX_MONITORING_EXCERPT_CHARS = 4_000
_CHECKPOINT_SCHEMA_READY = False


@dataclass(frozen=True)
class DiscoveryRoundResult:
    """单轮受控发现的脱敏汇总，不携带候选 URL 或正文。"""

    evidence_count: int
    saved_count: int
    candidate_count: int
    read_failure_count: int = 0
    duplicate_count: int = 0


@dataclass(frozen=True)
class SupplierResearchIdentity:
    """用于搜索查询和候选过滤的供应商确定性身份。"""

    supplier_id: int
    legal_name: str
    match_terms: tuple[str, ...]


_COMPANY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "有限公司",
    "集团公司",
    "公司",
)


def _normalize_entity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _supplier_identity_terms(supplier: Supplier, topic: str) -> tuple[str, ...]:
    values = [supplier.legal_name, supplier.registry_no or ""]
    values.extend(alias.alias for alias in supplier.aliases)
    legal_core = supplier.legal_name.strip()
    for suffix in _COMPANY_SUFFIXES:
        if legal_core.endswith(suffix):
            legal_core = legal_core[: -len(suffix)]
            break
    values.append(legal_core)
    normalized_legal_name = _normalize_entity_text(supplier.legal_name)
    normalized_topic = _normalize_entity_text(topic)
    for prefix_length in range(len(normalized_legal_name), 3, -1):
        legal_prefix = normalized_legal_name[:prefix_length]
        if legal_prefix in normalized_topic:
            values.append(legal_prefix)
            break
    terms: list[str] = []
    for value in values:
        normalized = _normalize_entity_text(value)
        if len(normalized) >= 4 and normalized not in terms:
            terms.append(normalized)
    return tuple(terms)


def _task_supplier_identities(
    session: Session, task: ResearchTask
) -> tuple[SupplierResearchIdentity, ...]:
    if not task.supplier_scope:
        return ()
    suppliers = list(
        session.scalars(
            select(Supplier)
            .options(selectinload(Supplier.aliases))
            .where(Supplier.id.in_(task.supplier_scope))
            .order_by(Supplier.id)
        )
    )
    return tuple(
        SupplierResearchIdentity(
            supplier_id=supplier.id,
            legal_name=supplier.legal_name,
            match_terms=_supplier_identity_terms(supplier, task.topic),
        )
        for supplier in suppliers
    )


def _supplier_scoped_query(
    query: str, identities: tuple[SupplierResearchIdentity, ...]
) -> str:
    if len(identities) != 1:
        return query
    identity = identities[0]
    if _normalize_entity_text(identity.legal_name) in _normalize_entity_text(query):
        return query
    return f'"{identity.legal_name}" {query}'


def _candidate_matches_supplier_scope(
    candidate: SearchCandidate,
    identities: tuple[SupplierResearchIdentity, ...],
) -> bool:
    if not identities:
        return True
    searchable = _normalize_entity_text(f"{candidate.title} {candidate.snippet}")
    return any(
        term in searchable
        for identity in identities
        for term in identity.match_terms
    )


def _source_read_failure_detail(error: BaseException) -> dict[str, object]:
    """提取可展示的失败分类，不把 URL、正文或原始异常写入事件。"""
    if isinstance(error, SourceRequestFailed):
        detail: dict[str, object] = {"error_kind": error.error_kind}
        if error.status_code is not None:
            detail["status_code"] = error.status_code
        return detail
    if isinstance(error, ValueError):
        return {"error_kind": "invalid_source"}
    if isinstance(error, SourceFetchError):
        detail = {"error_kind": error.error_kind or "source_fetch_failed"}
        if error.http_status is not None:
            detail["status_code"] = error.http_status
        return detail
    return {"error_kind": "read_failed"}


def _monitoring_match_has_precise_location(
    session: Session, *, event_id: int, supplier_id: int, match_type: str
) -> bool:
    """研究轨二次校验地点匹配，过滤旧数据中的跨区气象提醒。"""
    if not any(kind in match_type.split("+") for kind in ("site_text", "site_distance")):
        return True
    locations = list(
        session.scalars(select(EventLocation).where(EventLocation.event_id == event_id))
    )
    if not locations:
        return True
    supplier = session.scalar(
        select(Supplier)
        .options(selectinload(Supplier.sites))
        .where(Supplier.id == supplier_id)
    )
    if supplier is None:
        return False
    return any(
        matches_location_reference(
            LocationReference(
                name=location.name,
                country_code=location.country_code,
                region=location.region,
                city=location.city,
                district=location.district,
                latitude=location.latitude,
                longitude=location.longitude,
                radius_km=location.radius_km,
            ),
            site,
        )
        for location in locations
        for site in supplier.sites
    )


def postgres_checkpoint_connection_string(database_url: str = DATABASE_URL) -> str:
    """将 SQLAlchemy 地址转换为 psycopg checkpointer 所需的 libpq 地址。"""
    sqlalchemy_prefix = "postgresql+psycopg://"
    if database_url.startswith(sqlalchemy_prefix):
        return "postgresql://" + database_url.removeprefix(sqlalchemy_prefix)
    if database_url.startswith("postgresql://"):
        return database_url
    raise RuntimeError("LangGraph PostgreSQL checkpoint 仅支持 PostgreSQL 数据库地址")


@contextmanager
def _postgres_checkpointer() -> Iterator[PostgresSaver]:
    """在 LangGraph 路径使用数据库 checkpoint，且仅初始化一次官方表结构。"""
    global _CHECKPOINT_SCHEMA_READY
    with PostgresSaver.from_conn_string(postgres_checkpoint_connection_string()) as checkpointer:
        if not _CHECKPOINT_SCHEMA_READY:
            checkpointer.setup()
            _CHECKPOINT_SCHEMA_READY = True
        yield checkpointer


def _is_public_citation_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
    )


def _credibility_tier(score: int) -> str:
    if score >= 90:
        return "official"
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def load_monitoring_context(session: Session, task: ResearchTask) -> int:
    """把任务供应商范围内的合格监控信号保存为研究证据快照。"""
    append_task_event(
        session,
        task_id=task.id,
        event_type="monitoring_context_started",
        node_key="monitoring_context",
        parent_node_key="task",
        status="running",
        label="正在载入监控轨证据",
        detail={"supplier_count": len(task.supplier_scope)},
    )
    session.commit()
    if not task.supplier_scope:
        append_task_event(
            session,
            task_id=task.id,
            event_type="monitoring_context_skipped",
            node_key="monitoring_context",
            parent_node_key="task",
            status="skipped",
            label="未指定供应商，跳过监控轨证据",
        )
        session.commit()
        return 0

    latest_analysis_id = (
        select(AIAnalysisRecord.id)
        .where(
            AIAnalysisRecord.signal_id == RawSignal.id,
            AIAnalysisRecord.status == "succeeded",
        )
        .order_by(AIAnalysisRecord.started_at.desc(), AIAnalysisRecord.id.desc())
        .limit(1)
        .correlate(RawSignal)
        .scalar_subquery()
    )
    rows = session.execute(
        select(RiskAlert, RiskEvent, SupplierEventMatch, RawSignal, DataSource)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(RiskEventSignal, RiskEventSignal.event_id == RiskEvent.id)
        .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
        .join(DataSource, RawSignal.source_id == DataSource.id)
        .join(AIAnalysisRecord, AIAnalysisRecord.id == latest_analysis_id)
        .where(
            RiskAlert.status == "current",
            SupplierEventMatch.supplier_id.in_(task.supplier_scope),
            AIAnalysisRecord.needs_review.is_(False),
        )
        .order_by(RiskAlert.level, RiskAlert.updated_at.desc(), RiskAlert.id.desc())
    ).all()

    saved_count = 0
    filtered_location_count = 0
    seen_urls: set[str] = set()
    for alert, event, match, signal_record, data_source in rows:
        if not _monitoring_match_has_precise_location(
            session,
            event_id=match.event_id,
            supplier_id=match.supplier_id,
            match_type=match.match_type,
        ):
            filtered_location_count += 1
            continue
        if (
            not _is_public_citation_url(signal_record.url)
            or signal_record.url in seen_urls
        ):
            continue
        excerpt = normalize_research_text(signal_record.content)[
            :MAX_MONITORING_EXCERPT_CHARS
        ]
        if not excerpt:
            continue
        if saved_count >= MAX_MONITORING_CONTEXT_ITEMS:
            break
        source = ResearchSource(
            task_id=task.id,
            url=signal_record.url,
            title=normalize_research_text(signal_record.title)[:500] or None,
            source_type="monitoring_signal",
            credibility_tier=_credibility_tier(data_source.credibility),
            http_status=None,
            content_hash=excerpt_hash(excerpt),
            content_excerpt=excerpt,
            source_metadata={
                "origin": "monitoring_track",
                "alert_id": alert.id,
                "event_id": event.id,
                "signal_id": signal_record.id,
                "supplier_id": match.supplier_id,
                "risk_level": alert.level,
                "risk_score": alert.score,
                "source_code": data_source.code,
            },
        )
        session.add(source)
        session.flush()
        session.add(
            build_research_citation(
                task.id,
                source.id,
                quote=excerpt[:200],
                excerpt=excerpt,
                locator="监控轨原始信号正文",
            )
        )
        append_task_event(
            session,
            task_id=task.id,
            event_type="monitoring_signal_added",
            node_key=f"monitoring_signal:{signal_record.id}",
            parent_node_key="monitoring_context",
            status="succeeded",
            label="已加入监控轨风险信号",
            detail={
                "research_source_id": source.id,
                "signal_id": signal_record.id,
                "alert_id": alert.id,
                "supplier_id": match.supplier_id,
                "risk_level": alert.level,
            },
        )
        session.commit()
        seen_urls.add(signal_record.url)
        saved_count += 1

    append_task_event(
        session,
        task_id=task.id,
        event_type="monitoring_context_completed",
        node_key="monitoring_context",
        parent_node_key="task",
        status="succeeded",
        label="监控轨证据载入完成",
        detail={
            "source_count": saved_count,
            "filtered_location_count": filtered_location_count,
        },
    )
    session.commit()
    return saved_count


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
        claimed = claim_tool_run(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            action_type="public_page_read",
            arguments={"url": url},
        )
        if claimed is None:
            raise RuntimeError("受控单页任务租约无效")
        tool_run = claimed.tool_run
        if not claimed.claimed:
            if tool_run.status == "succeeded":
                source_id = tool_run.result_reference.get("research_source_id")
                source = (
                    session.get(ResearchSource, source_id)
                    if isinstance(source_id, int)
                    else None
                )
                if source is not None:
                    continue
                raise RuntimeError("已完成页面读取缺少可复用证据，停止以避免重复请求")
            raise RuntimeError("页面读取已有未完成或失败记录，停止以避免重复请求")
        reserved = reserve_task_usage(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            search_results_delta=1,
            current_step=f"controlled_page_reading:{index}/{len(source_urls)}",
        )
        if reserved is None:
            raise RuntimeError("受控单页任务租约无效")
        try:
            page = asyncio.run(read_public_page(url))
        except (ValueError, RuntimeError):
            failed = fail_tool_run(
                session,
                tool_run_id=tool_run.id,
                task_id=task.id,
                worker_id=task.worker_id or "",
                error_category="public_page_read_failed",
            )
            if failed is None:
                raise RuntimeError("受控单页任务租约无效") from None
            raise
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
        completed = complete_tool_run(
            session,
            tool_run_id=tool_run.id,
            task_id=task.id,
            worker_id=task.worker_id or "",
            result_reference={"research_source_id": source.id},
        )
        if completed is None:
            raise RuntimeError("受控单页任务租约无效")
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


def _execute_topic_source_discovery_round(
    session: Session,
    task: ResearchTask,
    *,
    query: str,
    phase: str,
    provider: SearchProvider | None = None,
    monitoring_count: int,
) -> DiscoveryRoundResult:
    """执行一轮固定查询的发现与受控读取，不生成报告。"""
    search_provider = provider or build_configured_search_provider()
    if search_provider is None:
        raise RuntimeError("研究搜索 Provider 未配置")

    search_node_key = "web_search" if phase == "primary" else "supplemental_search"
    source_node_prefix = "web_source" if phase == "primary" else "web_source:supplemental"
    search_event_prefix = "web_search" if phase == "primary" else "supplemental_search"
    supplier_identities = _task_supplier_identities(session, task)
    effective_query = _supplier_scoped_query(query, supplier_identities)
    provider_name = getattr(search_provider, "provider_name", "configured")

    remaining_results = _research_budget_limit(task, "max_results") - task.search_results_used
    requested_results = min(
        MAX_RESULTS_PER_QUERY,
        _research_max_pages(task),
        remaining_results,
    )
    if requested_results < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_results")

    claimed_search = claim_tool_run(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        action_type="web_search",
        arguments={
            "provider": provider_name,
            "query": effective_query,
            "max_results": requested_results,
        },
    )
    if claimed_search is None:
        raise RuntimeError("自动信源发现任务租约无效")
    search_run = claimed_search.tool_run
    if not claimed_search.claimed:
        if search_run.status == "succeeded":
            evidence_count = _verified_evidence_count(session, task)
            stored_candidate_count = search_run.result_reference.get("candidate_count", 0)
            if (
                evidence_count == 0
                and monitoring_count == 0
                and stored_candidate_count != 0
            ):
                raise RuntimeError("已完成搜索缺少可复用证据，停止以避免重复搜索")
            return DiscoveryRoundResult(
                evidence_count=evidence_count,
                saved_count=0,
                candidate_count=(
                    stored_candidate_count if isinstance(stored_candidate_count, int) else 0
                ),
            )
        raise RuntimeError("搜索已有未完成或失败记录，停止以避免重复调用")

    append_task_event(
        session,
        task_id=task.id,
        event_type=f"{search_event_prefix}_started",
        node_key=search_node_key,
        parent_node_key="task",
        status="running",
        label="正在搜索公开来源",
        detail={
            "provider": provider_name,
            "query": effective_query,
        },
    )
    reserved = reserve_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        search_queries_delta=1,
        current_step="searching_public_sources",
    )
    if reserved is None:
        raise RuntimeError("自动信源发现任务租约无效")

    quota_reservation = reserve_provider_quota(
        session,
        provider=provider_name,
        task_type=task.task_type,
        monthly_limit=get_search_settings().monthly_limit,
    )
    if quota_reservation is not None:
        search_run.usage_snapshot = {
            "provider_quota": quota_reservation.as_dict(),
        }
        session.commit()

    try:
        response = asyncio.run(
            run_search(
                search_provider,
                effective_query,
                budget=SearchBudget(max_queries=1, max_results=requested_results),
                max_results=requested_results,
            )
        )
    except Exception:  # noqa: BLE001 - 原异常由任务层记录，账本仅保留分类
        failed = fail_tool_run(
            session,
            tool_run_id=search_run.id,
            task_id=task.id,
            worker_id=task.worker_id or "",
            error_category="web_search_failed",
        )
        if failed is None:
            raise RuntimeError("自动信源发现任务租约无效") from None
        raise
    provider_candidate_count = len(response.results)
    relevant_candidates = tuple(
        candidate
        for candidate in response.results
        if _candidate_matches_supplier_scope(candidate, supplier_identities)
    )
    entity_filtered_count = provider_candidate_count - len(relevant_candidates)
    response = SearchResponse(
        provider_name=response.provider_name,
        query=response.query,
        results=relevant_candidates,
    )
    append_task_event(
        session,
        task_id=task.id,
        event_type=f"{search_event_prefix}_completed",
        node_key=search_node_key,
        parent_node_key="task",
        status="succeeded",
        label="公开来源搜索完成",
        detail={
            "provider": response.provider_name,
            "query": response.query,
            "provider_candidate_count": provider_candidate_count,
            "candidate_count": len(response.results),
            "entity_filtered_count": entity_filtered_count,
        },
    )
    session.commit()
    completed_search = complete_tool_run(
        session,
        tool_run_id=search_run.id,
        task_id=task.id,
        worker_id=task.worker_id or "",
        usage_snapshot={
            "search_queries": 1,
            "provider_candidate_count": provider_candidate_count,
            "candidate_count": len(response.results),
            "entity_filtered_count": entity_filtered_count,
        },
        result_reference={
            "provider": response.provider_name,
            "query": response.query,
            "provider_candidate_count": provider_candidate_count,
            "candidate_count": len(response.results),
            "entity_filtered_count": entity_filtered_count,
        },
    )
    if completed_search is None:
        raise RuntimeError("自动信源发现任务租约无效")
    if not response.results:
        append_task_event(
            session,
            task_id=task.id,
            event_type="source_read_summary",
            node_key=f"{source_node_prefix}:summary",
            parent_node_key=search_node_key,
            status="info",
            label=(
                "候选来源均未通过供应商实体校验"
                if entity_filtered_count
                else "没有候选来源需要读取"
            ),
            detail={
                "candidate_count": 0,
                "provider_candidate_count": provider_candidate_count,
                "entity_filtered_count": entity_filtered_count,
                "read_success_count": 0,
                "read_failure_count": 0,
                "duplicate_count": 0,
            },
        )
        session.commit()
        return DiscoveryRoundResult(
            evidence_count=_verified_evidence_count(session, task),
            saved_count=0,
            candidate_count=0,
        )

    saved_count = 0
    read_failure_count = 0
    duplicate_count = 0
    for index, candidate in enumerate(response.results, start=1):
        session.refresh(task)
        if task.cancel_requested_at is not None:
            append_task_event(
                session,
                task_id=task.id,
                event_type="source_read_summary",
                node_key=f"{source_node_prefix}:summary",
                parent_node_key=search_node_key,
                status="skipped",
                label="研究任务取消，停止读取候选来源",
                detail={
                    "candidate_count": len(response.results),
                    "read_success_count": saved_count,
                    "read_failure_count": read_failure_count,
                    "duplicate_count": duplicate_count,
                    "cancelled": True,
                },
            )
            session.commit()
            return DiscoveryRoundResult(
                evidence_count=_verified_evidence_count(session, task),
                saved_count=saved_count,
                candidate_count=len(response.results),
                read_failure_count=read_failure_count,
                duplicate_count=duplicate_count,
            )
        node_key = f"{source_node_prefix}:{index}"
        duplicate_url = session.scalar(
            select(ResearchSource.id).where(
                ResearchSource.task_id == task.id,
                ResearchSource.url == candidate.url,
            )
        )
        if duplicate_url is not None:
            duplicate_count += 1
            append_task_event(
                session,
                task_id=task.id,
                event_type="source_duplicate_skipped",
                node_key=node_key,
                parent_node_key=search_node_key,
                status="skipped",
                label="候选来源与已有证据重复",
                detail={"index": index, "duplicate_source_id": duplicate_url},
            )
            session.commit()
            continue
        claimed_read = claim_tool_run(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            action_type="public_page_read",
            arguments={"url": candidate.url},
        )
        if claimed_read is None:
            raise RuntimeError("自动信源发现任务租约无效")
        page_run = claimed_read.tool_run
        if not claimed_read.claimed:
            if page_run.status == "succeeded":
                source_id = page_run.result_reference.get("research_source_id")
                source = (
                    session.get(ResearchSource, source_id)
                    if isinstance(source_id, int)
                    else None
                )
                if source is not None:
                    append_task_event(
                        session,
                        task_id=task.id,
                        event_type="source_read_reused",
                        node_key=node_key,
                        parent_node_key=search_node_key,
                        status="skipped",
                        label="候选来源已由先前调用加入证据池",
                        detail={"index": index, "research_source_id": source_id},
                    )
                    session.commit()
                    continue
                raise RuntimeError("已完成页面读取缺少可复用证据，停止以避免重复请求")
            raise RuntimeError("页面读取已有未完成或失败记录，停止以避免重复请求")
        reserved = reserve_task_usage(
            session,
            task_id=task.id,
            worker_id=task.worker_id or "",
            search_results_delta=1,
            current_step=f"reading_discovered_source:{index}/{len(response.results)}",
        )
        if reserved is None:
            raise RuntimeError("自动信源发现任务租约无效")
        append_task_event(
            session,
            task_id=task.id,
            event_type="source_read_started",
            node_key=node_key,
            parent_node_key=search_node_key,
            status="running",
            label="正在读取候选来源",
            detail={"index": index, "candidate_count": len(response.results)},
        )
        session.commit()
        try:
            page = asyncio.run(read_public_page(candidate.url))
        except (ValueError, RuntimeError) as error:
            LOGGER.warning("自动发现候选来源读取失败：task_id=%s index=%s", task.id, index)
            failed = fail_tool_run(
                session,
                tool_run_id=page_run.id,
                task_id=task.id,
                worker_id=task.worker_id or "",
                error_category="public_page_read_failed",
            )
            if failed is None:
                raise RuntimeError("自动信源发现任务租约无效") from None
            read_failure_count += 1
            append_task_event(
                session,
                task_id=task.id,
                event_type="source_read_failed",
                node_key=node_key,
                parent_node_key=search_node_key,
                status="failed",
                label="候选来源读取失败",
                detail={"index": index, **_source_read_failure_detail(error)},
            )
            session.commit()
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
        duplicate_hash = session.scalar(
            select(ResearchSource.id).where(
                ResearchSource.task_id == task.id,
                ResearchSource.content_hash == source.content_hash,
            )
        )
        if duplicate_hash is not None:
            duplicate_count += 1
            completed_read = complete_tool_run(
                session,
                tool_run_id=page_run.id,
                task_id=task.id,
                worker_id=task.worker_id or "",
                result_reference={"duplicate_source_id": duplicate_hash},
            )
            if completed_read is None:
                raise RuntimeError("自动信源发现任务租约无效")
            append_task_event(
                session,
                task_id=task.id,
                event_type="source_duplicate_skipped",
                node_key=node_key,
                parent_node_key=search_node_key,
                status="skipped",
                label="候选正文与已有证据重复",
                detail={"index": index, "duplicate_source_id": duplicate_hash},
            )
            session.commit()
            continue
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
        append_task_event(
            session,
            task_id=task.id,
            event_type="source_added",
            node_key=node_key,
            parent_node_key=search_node_key,
            status="succeeded",
            label="公开来源已加入证据池",
            detail={
                "index": index,
                "research_source_id": source.id,
                "reader": page.reader,
            },
        )
        session.commit()
        completed_read = complete_tool_run(
            session,
            tool_run_id=page_run.id,
            task_id=task.id,
            worker_id=task.worker_id or "",
            result_reference={"research_source_id": source.id},
        )
        if completed_read is None:
            raise RuntimeError("自动信源发现任务租约无效")
        saved_count += 1

    append_task_event(
        session,
        task_id=task.id,
        event_type="source_read_summary",
        node_key=f"{source_node_prefix}:summary",
        parent_node_key=search_node_key,
        status=("succeeded" if saved_count else "skipped" if duplicate_count else "failed"),
        label=(
            "候选来源读取完成"
            if saved_count
            else "候选来源均已存在于证据池"
            if duplicate_count
            else "候选来源均无法形成证据"
        ),
        detail={
            "candidate_count": len(response.results),
            "read_success_count": saved_count,
            "read_failure_count": read_failure_count,
            "duplicate_count": duplicate_count,
        },
    )
    session.commit()
    return DiscoveryRoundResult(
        evidence_count=_verified_evidence_count(session, task),
        saved_count=saved_count,
        candidate_count=len(response.results),
        read_failure_count=read_failure_count,
        duplicate_count=duplicate_count,
    )


def execute_topic_source_discovery(
    session: Session,
    task: ResearchTask,
    *,
    provider: SearchProvider | None = None,
    report_provider: AIProvider | None = None,
    monitoring_count: int | None = None,
) -> None:
    """legacy 路径：单次发现后直接生成报告，不进入补充搜索分支。"""
    effective_monitoring_count = (
        monitoring_count if monitoring_count is not None else load_monitoring_context(session, task)
    )
    result = _execute_topic_source_discovery_round(
        session,
        task,
        query=task.topic,
        phase="primary",
        provider=provider,
        monitoring_count=effective_monitoring_count,
    )
    if result.evidence_count < MIN_VERIFIED_EVIDENCE:
        _create_public_evidence_insufficient_report(session, task)
        return
    _generate_report_draft(
        session,
        task,
        provider=report_provider or get_ai_provider(get_ai_settings()),
    )
    updated = record_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        current_step=f"report_draft_created:{result.saved_count}/{result.candidate_count}",
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
    claimed_report = claim_tool_run(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        action_type="report_generation",
        arguments={
            "citation_ids": sorted(evidence.citation_id for evidence in context.evidence),
            "model": provider.model,
        },
    )
    if claimed_report is None:
        raise RuntimeError("研究报告任务租约无效")
    report_run = claimed_report.tool_run
    existing_report = session.scalar(
        select(ResearchReport)
        .where(ResearchReport.task_id == task.id)
        .order_by(ResearchReport.id.desc())
    )
    if not claimed_report.claimed:
        if report_run.status == "succeeded" and existing_report is not None:
            return
        if report_run.status == "running" and existing_report is not None:
            completed_report = complete_tool_run(
                session,
                tool_run_id=report_run.id,
                task_id=task.id,
                worker_id=task.worker_id or "",
                result_reference={"report_id": existing_report.id, "recovered": True},
            )
            if completed_report is None:
                raise RuntimeError("研究报告任务租约无效")
            return
        raise RuntimeError("报告生成已有未完成、失败或不可复用记录，停止以避免重复调用")
    remaining_input = _research_budget_limit(task, "max_input_tokens") - task.input_tokens_used
    remaining_output = (
        _research_budget_limit(task, "max_output_tokens") - task.output_tokens_used
    )
    if remaining_input < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_input_tokens")
    if remaining_output < 1:
        raise ResearchBudgetExceeded("研究任务超过预算：max_output_tokens")

    append_task_event(
        session,
        task_id=task.id,
        event_type="report_generation_started",
        node_key="report",
        parent_node_key="task",
        status="running",
        label="正在生成研究报告草稿",
        detail={
            "provider": provider.provider_name,
            "model": provider.model,
            "evidence_count": len(context.evidence),
        },
    )
    session.commit()
    generated = None
    try:
        generated = asyncio.run(
            provider.generate_research_report(context, max_output_tokens=remaining_output)
        )
        draft = canonicalize_generated_report(generated.draft, context)
    except Exception as error:  # noqa: BLE001 - 事件和账本只记录脱敏分类
        input_tokens = generated.input_tokens if generated is not None else 0
        output_tokens = generated.output_tokens if generated is not None else 0
        if generated is not None:
            updated = record_task_usage(
                session,
                task_id=task.id,
                worker_id=task.worker_id or "",
                input_tokens_delta=input_tokens,
                output_tokens_delta=output_tokens,
                current_step="report_generation_failed",
            )
            if updated is None:
                raise RuntimeError("研究报告任务租约无效") from error
            report_run.usage_snapshot = {
                **report_run.usage_snapshot,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            session.commit()
        error_category = (
            "report_validation_failed"
            if isinstance(error, ReportValidationError)
            else "report_generation_failed"
        )
        failed = fail_tool_run(
            session,
            tool_run_id=report_run.id,
            task_id=task.id,
            worker_id=task.worker_id or "",
            error_category=error_category,
        )
        if failed is None:
            raise RuntimeError("研究报告任务租约无效") from None
        append_task_event(
            session,
            task_id=task.id,
            event_type="report_generation_failed",
            node_key="report",
            parent_node_key="task",
            status="failed",
            label="研究报告草稿生成失败",
            detail={
                "provider": provider.provider_name,
                "model": provider.model,
                "error_kind": error_category,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        session.commit()
        raise
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
    completed_report = complete_tool_run(
        session,
        tool_run_id=report_run.id,
        task_id=task.id,
        worker_id=task.worker_id or "",
        usage_snapshot={
            "input_tokens": generated.input_tokens,
            "output_tokens": generated.output_tokens,
        },
        result_reference={"report_id": report.id},
    )
    if completed_report is None:
        raise RuntimeError("研究报告任务租约无效")
    write_audit(
        session,
        action="research_report_draft_generated",
        resource_type="research_report",
        resource_id=str(report.id),
        detail=f"task_id={task.id};provider={provider.provider_name};model={provider.model}",
    )
    append_task_event(
        session,
        task_id=task.id,
        event_type="report_generated",
        node_key="report",
        parent_node_key="task",
        status="succeeded",
        label="研究报告草稿已生成",
        detail={
            "provider": provider.provider_name,
            "model": provider.model,
            "report_id": report.id,
            "evidence_count": len(context.evidence),
            "input_tokens": generated.input_tokens,
            "output_tokens": generated.output_tokens,
        },
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


def _verified_evidence_count(session: Session, task: ResearchTask) -> int:
    """按报告实际可用的回验引用统计证据，避免图状态保存正文。"""
    try:
        return len(_report_generation_input(session, task).evidence)
    except RuntimeError:
        return 0


def _supplemental_search_query(topic: str) -> str:
    """平台派生的固定补充查询，不接受模型或用户的任意工具参数。"""
    return f"{topic} 风险 公告"


def _can_run_supplemental_search(task: ResearchTask, evidence_count: int) -> bool:
    return (
        evidence_count < MIN_VERIFIED_EVIDENCE
        and task.cancel_requested_at is None
        and task.search_queries_used < _research_budget_limit(task, "max_queries")
    )


def _create_public_evidence_insufficient_report(session: Session, task: ResearchTask) -> None:
    """在没有已回验证据时生成明确的人工复核草稿，不调用模型。"""
    claimed_report = claim_tool_run(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        action_type="report_generation",
        arguments={"mode": "public_evidence_insufficient"},
    )
    if claimed_report is None:
        raise RuntimeError("研究报告任务租约无效")
    report_run = claimed_report.tool_run
    existing_report = session.scalar(
        select(ResearchReport)
        .where(ResearchReport.task_id == task.id)
        .order_by(ResearchReport.id.desc())
    )
    if not claimed_report.claimed:
        if report_run.status == "succeeded" and existing_report is not None:
            return
        if report_run.status == "running" and existing_report is not None:
            completed_report = complete_tool_run(
                session,
                tool_run_id=report_run.id,
                task_id=task.id,
                worker_id=task.worker_id or "",
                result_reference={"report_id": existing_report.id, "recovered": True},
            )
            if completed_report is None:
                raise RuntimeError("研究报告任务租约无效")
            return
        raise RuntimeError("公开证据不足报告已有未完成、失败或不可复用记录")

    draft = ResearchReportDraft(
        title=f"{task.topic}：公开证据不足",
        disclaimer=f"{REQUIRED_DISCLAIMER}。本次受控公开检索未获得可回验的有效证据，需人工补充来源后再判断。",
    )
    report = create_generated_report(
        session,
        task_id=task.id,
        draft=draft,
        model_version="system-insufficient-evidence-v1",
    )
    completed_report = complete_tool_run(
        session,
        tool_run_id=report_run.id,
        task_id=task.id,
        worker_id=task.worker_id or "",
        result_reference={"report_id": report.id},
    )
    if completed_report is None:
        raise RuntimeError("研究报告任务租约无效")
    updated = record_task_usage(
        session,
        task_id=task.id,
        worker_id=task.worker_id or "",
        current_step="public_evidence_insufficient",
    )
    if updated is None:
        raise RuntimeError("研究任务租约无效")
    append_task_event(
        session,
        task_id=task.id,
        event_type="report_insufficient_evidence",
        node_key="report",
        parent_node_key="task",
        status="info",
        label="公开证据不足，已生成待人工补充草稿",
        detail={"report_id": report.id},
    )
    session.commit()


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
    orchestrator: str = RESEARCH_ORCHESTRATOR,
) -> ResearchTask | None:
    """只认领已显式开始的手动研究任务并自动发现来源。"""
    if orchestrator not in {"legacy", "langgraph"}:
        raise ValueError("研究编排器仅支持 legacy 或 langgraph")

    def execute(current_session: Session, task: ResearchTask) -> None:
        if orchestrator == "legacy":
            execute_topic_source_discovery(
                current_session,
                task,
                report_provider=report_provider,
            )
            return
        execute_langgraph_topic_source_discovery(
            current_session,
            task,
            report_provider=report_provider,
        )

    return run_once(
        session,
        worker_id=worker_id,
        execute=execute,
        lease_seconds=lease_seconds,
        # 主题 Worker 同时消费用户已开始的 manual 和 Scheduler 已授权的 monthly。
        task_type=None,
        require_execution_requested=True,
    )


def execute_langgraph_topic_source_discovery(
    session: Session,
    task: ResearchTask,
    *,
    report_provider: AIProvider | None = None,
) -> None:
    """通过受控图运行既有主题发现流程，保留平台作为业务事实来源。"""
    monitoring_count = 0
    append_task_event(
        session,
        task_id=task.id,
        event_type="agent_graph_started",
        node_key="agent_graph",
        parent_node_key="execution",
        status="running",
        label="受控研究图已启动",
        detail={"graph_version": RESEARCH_GRAPH_VERSION},
        once=True,
    )
    task.graph_version = RESEARCH_GRAPH_VERSION
    task.checkpoint_thread_id = f"research-task-{task.id}-{RESEARCH_GRAPH_VERSION}"
    session.commit()

    def load_context() -> int:
        nonlocal monitoring_count
        monitoring_count = load_monitoring_context(session, task)
        return monitoring_count

    def execute_primary_discovery() -> int:
        return _execute_topic_source_discovery_round(
            session,
            task,
            query=task.topic,
            phase="primary",
            monitoring_count=monitoring_count,
        ).evidence_count

    def execute_supplemental_discovery() -> int:
        return _execute_topic_source_discovery_round(
            session,
            task,
            query=_supplemental_search_query(task.topic),
            phase="supplemental",
            monitoring_count=monitoring_count,
        ).evidence_count

    def should_run_supplemental_search(evidence_count: int) -> bool:
        session.refresh(task)
        return _can_run_supplemental_search(task, evidence_count)

    def compose_report(outcome: EvidenceOutcome) -> None:
        if outcome == "evidence_sufficient":
            _generate_report_draft(
                session,
                task,
                provider=report_provider or get_ai_provider(get_ai_settings()),
            )
            return
        _create_public_evidence_insufficient_report(session, task)

    config: RunnableConfig = {
        "configurable": {"thread_id": task.checkpoint_thread_id}
    }
    with _postgres_checkpointer() as checkpointer:
        graph = build_topic_source_discovery_graph(
            load_monitoring_context=load_context,
            execute_primary_discovery=execute_primary_discovery,
            execute_supplemental_discovery=execute_supplemental_discovery,
            should_run_supplemental_search=should_run_supplemental_search,
            compose_report=compose_report,
            checkpointer=checkpointer,
        )
        checkpoint = graph.get_state(config)
        initial_state: ResearchGraphState = {
            "task_id": task.id,
            "graph_version": RESEARCH_GRAPH_VERSION,
        }
        result = graph.invoke(
            None if checkpoint.next else initial_state,
            config=config,
        )
    append_task_event(
        session,
        task_id=task.id,
        event_type="agent_graph_completed",
        node_key="agent_graph",
        parent_node_key="execution",
        status="succeeded",
        label="受控研究图已完成",
        detail={
            "graph_version": RESEARCH_GRAPH_VERSION,
            "monitoring_source_count": result.get("monitoring_source_count", 0),
        },
        once=True,
    )
    session.commit()


def _run_heartbeat_loop(
    *,
    worker_id: str,
    mode: str,
    orchestrator: str,
    interval_seconds: float,
    stop_event: Event,
    session_factory: Callable[[], Session],
) -> None:
    """使用独立会话续期，避免长时间外部调用阻塞 Worker 心跳。"""
    while not stop_event.wait(interval_seconds):
        try:
            with session_factory() as session:
                if touch_worker_heartbeat(session, worker_id=worker_id) is None:
                    upsert_worker_heartbeat(
                        session,
                        worker_id=worker_id,
                        mode=mode,
                        orchestrator=orchestrator,
                    )
        except Exception:  # noqa: BLE001 - 心跳失败不应中断正在执行的受控任务
            LOGGER.exception("research-worker 心跳续期失败：worker_id=%s", worker_id)


def run_forever(
    *,
    worker_id: str,
    lease_seconds: int,
    poll_seconds: float,
    stop_event: Event,
    run_task_once: Callable[[Session, str, int], ResearchTask | None],
    session_factory: Callable[[], Session] = SessionLocal,
    heartbeat_mode: str | None = None,
    heartbeat_orchestrator: str | None = None,
    heartbeat_interval_seconds: float = RESEARCH_WORKER_HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """轮询本地测试队列，并为常驻 Worker 写入可诊断心跳。"""
    heartbeat_enabled = heartbeat_mode is not None and heartbeat_orchestrator is not None
    heartbeat_stop = Event()
    heartbeat_thread: Thread | None = None
    if heartbeat_enabled:
        with session_factory() as session:
            upsert_worker_heartbeat(
                session,
                worker_id=worker_id,
                mode=heartbeat_mode or "",
                orchestrator=heartbeat_orchestrator or "",
            )
        heartbeat_thread = Thread(
            target=_run_heartbeat_loop,
            kwargs={
                "worker_id": worker_id,
                "mode": heartbeat_mode or "",
                "orchestrator": heartbeat_orchestrator or "",
                "interval_seconds": heartbeat_interval_seconds,
                "stop_event": heartbeat_stop,
                "session_factory": session_factory,
            },
            name=f"research-heartbeat:{worker_id}",
            daemon=True,
        )
        heartbeat_thread.start()
    try:
        while not stop_event.is_set():
            with session_factory() as session:
                completed = run_task_once(session, worker_id, lease_seconds)
            if completed is None:
                stop_event.wait(poll_seconds)
    finally:
        if heartbeat_enabled:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=max(1.0, heartbeat_interval_seconds * 2))
            with session_factory() as session:
                stop_worker_heartbeat(session, worker_id=worker_id)


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
        heartbeat_mode=RESEARCH_WORKER_MODE,
        heartbeat_orchestrator=RESEARCH_ORCHESTRATOR,
    )


if __name__ == "__main__":
    main()
