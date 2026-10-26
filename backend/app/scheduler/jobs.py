"""独立 Scheduler 进程（技术方案 5.1：Web 与调度分离）。

定时任务：
- collect_job / collect_source_job / collect_tyc_shard_job：只负责采集入库，
  不再在采集后同步消费队列（消费由 scheduler.consume 的独立循环负责）。
- expire_job：定期将超过 expires_at 的提醒标记为 expired。
- cleanup_job：每日数据保留清理（90 天 / 30 天轮转）。

启动方式：python -m app.scheduler.main
"""

from __future__ import annotations

import asyncio
import logging
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.agent.tyc_batch import (
    TycBatchBucketGateRejected,
    TycBatchLocked,
    TycBatchSourceInactive,
    TycBatchUnavailable,
    run_tyc_batch,
)
from app.agent.tyc_batch_models import SHARD_COUNT
from app.agent.tyc_gateway import TYC_SOURCE_CODE
from app.ai.models import AIAnalysisRecord
from app.ai.service import (
    analyze_raw_signal,
    latest_reusable_analysis,
    usable_analysis_result,
)
from app.auth.models import User
from app.auth.security import write_audit
from app.config import (
    RESEARCH_DAILY_TOPIC,
    RESEARCH_MONTHLY_CRON,
    RESEARCH_MONTHLY_ENABLED,
    RESEARCH_MONTHLY_TOPIC,
    RESEARCH_ORCHESTRATOR,
    RESEARCH_SCHEDULE_OWNER_USERNAME,
    RESEARCH_TOOL_RUN_STALE_SECONDS,
    RESEARCH_WEEKLY_TOPIC,
    SIGNAL_ANALYZE_BATCH,
    SIGNAL_PRIORITY_AGING_HOURS,
    SIGNAL_PRIORITY_ENABLED,
    SIGNAL_RELEVANCE_FILTER_ENABLED,
)
from app.database import SessionLocal
from app.market.config_store import load_market_config
from app.market.evaluation import (
    DETERMINISTIC_PROVIDER,
    build_filtered_reason,
    build_result,
    evaluate_series,
    load_series,
    resolve_episode_start,
)
from app.market.registry import resolve_indicator
from app.research.models import ResearchBatch, ResearchTask
from app.research.schedule import (
    get_schedule_config,
    monthly_schedule_preflight,
    select_monthly_suppliers,
    weekly_schedule_preflight,
)
from app.research.service import (
    DEFAULT_MONTHLY_RESEARCH_BUDGET,
    DEFAULT_RESEARCH_BUDGET,
    create_task,
    reconcile_stale_tool_runs,
)
from app.risks.intelligence_batch import generate_pending_intelligence_reports
from app.risks.models import RiskEventSignal
from app.risks.service import expire_alerts, process_analysis
from app.risks.validity import expire_signal_if_due, is_raw_signal_effective
from app.scheduler import runtime
from app.scheduler.retention import cleanup_retention
from app.scheduler.run_results import RunResult, RunResultMetric
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.relevance import (
    assess_signal_relevance,
    grade_structured_signal,
    load_filter_rules,
)
from app.signals.router import build_pull_adapter
from app.signals.service import CollectionDeferred, CollectionFailed, collect_source
from app.signals.validity import AnchorSource, ProcessingState
from app.suppliers.models import Supplier
from app.suppliers.schemas import normalize_alias

logger = logging.getLogger("scheduler")


def _matches_any_supplier(session: Session, text: str) -> bool:
    """供应商主体/别名是否命中文本（与相关性过滤同款匹配）。"""
    normalized = normalize_alias(text)
    for supplier in session.scalars(
        select(Supplier).where(Supplier.enabled.is_(True))
    ):
        if normalize_alias(supplier.legal_name) in normalized:
            return True
        for alias in supplier.aliases:
            if alias.normalized_alias and alias.normalized_alias in normalized:
                return True
    return False

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
# ponytail: 当前生产仅单 Scheduler 进程；扩为多副本前改用数据库级原子领取或 advisory lock。
_pending_signal_processing_lock = Lock()

# 确定性预筛三类过滤器的 details.filter 取值（与 AIAnalysisRecord.prompt_version 对应可追溯）。
RELEVANCE_FILTER_KIND = "relevance"
ENTITY_LIST_FILTER_KIND = "entity_list"
STRUCTURED_GRADE_FILTER_KIND = "structured_grade"
# 行情指标评估：不写 filtered 语义的过滤记录，而是自带 deterministic 结果的
# 成功分析记录（未触发时结果为空），因此 filter_kind 独立于上面三类。
MARKET_FILTER_KIND = "market_indicator"
# 判定总开关关闭时的终态：只记录读数、不做结论。与上面的 filter_kind 区分开，
# 前端据此显示「仅记录（未判定）」而不是「已过滤/不相关」。
MARKET_RECORD_KIND = "market_recorded"
# 确定性行情评估的模型与提示版本标识（纯统计复算，不对应任何真实模型）。
MARKET_PROMPT_VERSION = "market-eval-v1"


def _mark_signal_irrelevant(
    signal: RawSignal,
    *,
    filter_kind: str,
    reason: str,
    state: ProcessingState = ProcessingState.FILTERED,
) -> None:
    """把确定性预筛未通过（或行情未触发）的信号落到处理阶段。

    只改 ``processing_state``：相关性/清单/结构化分级为 ``filtered``，行情未触发为
    ``no_trigger``；``validity_state``（生命周期）保持原值不被覆盖——过滤结论不是
    生命周期。与过滤记录（AIAnalysisRecord status=succeeded 且结果为空）同一事务
    写入，使信号不再以 pending_classification 长期滞留、也不被队列谓词反复捞取；
    计数为 0 的过滤信号不计有效信号，判活谓词 valid_signal_condition 的 ACTIVE/LEGACY
    allow-list 天然排除。调用方负责 commit。
    """
    signal.processing_state = state
    signal.validity_reason = {
        "code": "filtered_out",
        "anchor_source": (
            AnchorSource.PUBLISHED_AT
            if signal.published_at is not None
            else AnchorSource.COLLECTED_AT
        ),
        "details": {"filter": filter_kind, "reason": reason},
    }


def _collect_enabled_sources(
    *, source_ids: list[int] | None = None, only_without_schedule: bool = False
) -> dict[str, int]:
    """采集启用的拉取式信息源，返回 ``{source_code: created_count}``。"""
    summary: dict[str, int] = {}
    with SessionLocal() as session:
        filters = [DataSource.enabled.is_(True)]
        if source_ids is not None:
            filters.append(DataSource.id.in_(source_ids))
        if only_without_schedule:
            filters.append(DataSource.schedule.is_(None))
        sources = list(session.scalars(select(DataSource).where(*filters).order_by(DataSource.id)))
        for source in sources:
            try:
                pull_adapter = build_pull_adapter(source)
            except Exception as exc:  # 非拉取式（manual-json）或未实现
                logger.info("跳过信息源 %s（非拉取式）: %s", source.code, exc)
                continue
            # 任务开始写观测（独立短事务；写失败不影响采集业务）。
            runtime.record_job_started(
                runtime.source_collection_job_key(source.id),
                kind="source_collection",
                stage="collecting",
            )
            try:
                run = collect_source(session, source, pull_adapter)
            except CollectionDeferred as exc:
                # 请求受控延后：中性完成，不写失败观测、不计入失败摘要。
                logger.info("信息源 %s 采集受控延后: %s", source.code, exc)
                runtime.record_source_collection_deferred(source.id)
                continue
            except CollectionFailed as exc:
                logger.error("信息源 %s 采集失败: %s", source.code, exc)
                summary[source.code] = -1
                runtime.record_source_collection(source.id, succeeded=False)
                continue
            except Exception:
                # 采集链意外异常：先写 failed 观测再原样抛出，不把中断伪装成成功。
                runtime.record_source_collection(source.id, succeeded=False)
                raise
            summary[source.code] = run.created_count
            runtime.record_source_collection(source.id, succeeded=True)
            logger.info(
                "信息源 %s 采集完成: fetched=%d created=%d dup=%d",
                source.code,
                run.fetched_count,
                run.created_count,
                run.duplicate_count,
            )
    return summary


def collect_source_job(source_id: int) -> RunResult | None:
    """按信息源自己的 cron 触发一次采集（只入库，不阻塞消费队列）。

    成功时用最近一次 ``CollectionRun`` 的抓取/新增/重复计数生成脱敏摘要；
    非拉取源、受控延后、采集失败与异常均不返回摘要。
    """
    try:
        summary = _collect_enabled_sources(source_ids=[source_id])
        if not summary:
            return None
        # _collect_enabled_sources 的键是 source_code，失败值为 -1。
        if any(value < 0 for value in summary.values()):
            return None
        logger.info("信息源独立调度完成: %s", summary)
        with SessionLocal() as session:
            run = session.scalar(
                select(CollectionRun)
                .where(CollectionRun.source_id == source_id)
                .order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
                .limit(1)
            )
            if run is None:
                return None
            fetched, created_count, duplicate = (
                run.fetched_count,
                run.created_count,
                run.duplicate_count,
            )
        return RunResult(
            summary="采集完成",
            metrics=[
                RunResultMetric(label="抓取", value=max(0, fetched)),
                RunResultMetric(label="新增", value=max(0, created_count)),
                RunResultMetric(label="重复", value=max(0, duplicate)),
            ],
            outputs=[RunResultMetric(label="新增信号", value=max(0, created_count))],
        )
    except Exception as exc:
        logger.exception("信息源 %s 独立调度异常: %s", source_id, exc)
        return None


# 天眼查周度分片核查的权威触发 cadence（D8：固定 2 片）：
# 周日跑 shard 0、周一跑 shard 1，同一时刻 06:00 Asia/Shanghai。
# 使用具名星期（sun/mon）避免标准 cron 数字星期与 APScheduler 的歧义。
TYC_SHARD_CRONS: tuple[str, str] = ("0 6 * * sun", "0 6 * * mon")
# 信源层健康新鲜度只按周度窗口推导：取首个分片日（周日）作为 7 天窗口锚点，
# 避免周日/周一两个分片日之间或多分片覆盖期间误报 overdue —— 单供应商在
# 分片周期内未刷新属正常，只有越过整个周度窗口才算真超期。
TYC_WEEKLY_SCHEDULE: str = TYC_SHARD_CRONS[0]


def collect_tyc_shard_job(shard_index: int) -> RunResult | None:
    """周度分片核查启用的供应商：调用天眼查 MCP 并把多维度报告写入信号池。

    只处理所属 SHA-256 分片（固定 2 片）的 ``suppliers.enabled=true``；启用前
    按**真实桶门禁**校验 ``max_bucket × (1+len(dimensions)) ≤ daily_limit``，
    拒绝时记跳过日志、**不部分执行**；批次锁被占用（另一分片或手动核查运行中）
    同样跳过。批量逻辑复用 ``app.agent.tyc_batch.run_tyc_batch``（与手动单供应商
    核查同一额度执行器）：逐工具写 ``tyc_usage_records``，多维度报告落信号池。

    采集只入库、不消费：新增信号交由独立消费循环（``scheduler.consume``）分析。
    成功且有目标供应商时返回计数摘要；未启用/额度不可用/门禁拒绝/锁繁忙/
    无目标/异常一律降级为跳过日志并返回 None，异常不外抛。
    """
    try:
        with SessionLocal() as session:
            source = session.scalar(
                select(DataSource).where(DataSource.code == TYC_SOURCE_CODE)
            )
            if source is None:
                logger.warning("天眼查信息源未配置，跳过供应商分片核查")
                return None
            try:
                result = run_tyc_batch(
                    session,
                    source,
                    shard_index=shard_index,
                    shard_count=SHARD_COUNT,
                )
            except TycBatchSourceInactive:
                logger.warning("天眼查未启用，跳过供应商分片核查")
                return None
            except TycBatchUnavailable as exc:
                logger.warning("天眼查不可用，跳过供应商分片核查：%s", exc)
                return None
            except TycBatchLocked as exc:
                logger.warning("天眼查分片 %d 跳过：%s", shard_index, exc)
                return None
            except TycBatchBucketGateRejected as exc:
                logger.warning(
                    "天眼查分片 %d 门禁拒绝，跳过（不部分执行）：%s", shard_index, exc
                )
                return None
        if result.targeted_count == 0:
            logger.info("天眼查分片 %d 无启用供应商，跳过", shard_index)
            return None
        logger.info(
            "天眼查分片 %d 核查完成: 供应商=%d 调用=%d 新增信号=%d 重复=%d 空=%d "
            "失败=%d 额度耗尽=%s",
            shard_index,
            result.targeted_count,
            result.attempted_count,
            result.created_count,
            result.duplicate_count,
            result.empty_count,
            result.failed_count,
            result.quota_exhausted,
        )
        return RunResult(
            summary="天眼查核查完成",
            metrics=[
                RunResultMetric(label="供应商", value=max(0, result.targeted_count)),
                RunResultMetric(label="调用", value=max(0, result.attempted_count)),
                RunResultMetric(label="新增信号", value=max(0, result.created_count)),
                RunResultMetric(label="重复", value=max(0, result.duplicate_count)),
                RunResultMetric(label="空结果", value=max(0, result.empty_count)),
                RunResultMetric(label="失败", value=max(0, result.failed_count)),
            ],
        )
    except Exception as exc:
        logger.exception("天眼查分片核查异常: %s", exc)
        return None


def _evaluate_market_signal(
    session: Session, signal: RawSignal, source_code: str, *, now_utc: datetime
) -> tuple[AIAnalysisRecord | None, bool]:
    """已登记行情指标走确定性评估（免 LLM）。

    返回 ``(分析记录, 是否已终态处理)``：

    - ``(None, False)``：不命中任何**启用中**的登记指标——未登记品种、指标被运行期
      停用、或历史行没有 / 不可解析 ``source_extra``。这不是错误：调用方必须原样
      继续既有路径（commodity 仍由旧分级兜底，其余走相关性 + AI）。
    - ``(None, True)``：命中但未触发（还在积累 / 未达触发线 / 无可用观察点）：写终态
      过滤记录并置 irrelevant，本条不再进入后续批次。
    - ``(record, True)``：已触发；``record`` 是可直接复用的确定性结果，调用方继续
      ``process_analysis``，**不经过任何 AI Provider**。

    未触发时 ``needs_review=False``：判定是纯统计复算，没有模型不确定性需要人工复核
    （与相关性/清单/结构化分级兜底不同，那三类是启发式近似）。
    """
    raw_data = signal.raw_data
    extra = raw_data.get("source_extra") if isinstance(raw_data, dict) else None
    config = load_market_config(session)
    spec = resolve_indicator(source_code, extra, config)
    if spec is None:
        return None, False
    if not config.analysis_enabled:
        # 判定总开关关闭（默认）：行情只做记录。仍写一条确定性记录并终结该信号，
        # 否则它会以 pending 滞留、被队列反复捞取，或回落进 AI 相关性分析。
        # 不调用 evaluate_series：不计算涨跌幅、不触发事件、不产提醒。
        session.add(
            AIAnalysisRecord(
                signal_id=signal.id,
                provider=DETERMINISTIC_PROVIDER,
                model=MARKET_PROMPT_VERSION,
                prompt_version=MARKET_PROMPT_VERSION,
                status="succeeded",
                finished_at=now_utc,
                duration_ms=0,
                result=None,
                error="recorded: 行情指标仅记录，判定总开关已关闭",
                needs_review=False,
            )
        )
        _mark_signal_irrelevant(
            signal,
            filter_kind=MARKET_RECORD_KIND,
            reason="行情指标仅记录，不参与阈值判定与风险提醒",
            state=ProcessingState.NO_TRIGGER,
        )
        session.commit()
        return None, True
    series = load_series(session, spec, source_id=signal.source_id)
    verdict = evaluate_series(spec, series)
    if not verdict.triggered:
        reason = build_filtered_reason(spec, verdict)
        detail = f"{reason.code}｜{reason.detail_zh}"
        session.add(
            AIAnalysisRecord(
                signal_id=signal.id,
                provider=DETERMINISTIC_PROVIDER,
                model=MARKET_PROMPT_VERSION,
                prompt_version=MARKET_PROMPT_VERSION,
                status="succeeded",
                finished_at=now_utc,
                duration_ms=0,
                result=None,
                error=f"filtered: {detail}",
                needs_review=False,
            )
        )
        _mark_signal_irrelevant(
            signal,
            filter_kind=MARKET_FILTER_KIND,
            reason=detail,
            # 行情未触发是「已确定性评估、未达触发线」，不是业务无关过滤。
            state=ProcessingState.NO_TRIGGER,
        )
        session.commit()
        return None, True
    result = build_result(
        spec,
        verdict,
        start_at=resolve_episode_start(
            session, spec, source_id=signal.source_id, series=series, now=now_utc
        ),
    )
    record = AIAnalysisRecord(
        signal_id=signal.id,
        provider=DETERMINISTIC_PROVIDER,
        model=MARKET_PROMPT_VERSION,
        prompt_version=MARKET_PROMPT_VERSION,
        status="succeeded",
        finished_at=now_utc,
        duration_ms=0,
        result=result.model_dump(mode="json"),
        needs_review=False,
    )
    session.add(record)
    session.flush()
    return record, True


@dataclass(frozen=True)
class BatchResult:
    """单批信号消费的计数结果（不含并发控制与观测写入）。"""

    processed: int
    filtered: int
    failed: int


def process_signal_batch(batch: int, *, now_utc: datetime | None = None) -> BatchResult:
    """消费最多 ``batch`` 条候选信号，返回本批 processed/filtered/failed 计数。

    事务主体与既有处理链逐字一致：候选查询 → 有效期收敛 → 可复用分析 →
    确定性行情评估 → 相关性预过滤 → 清单未命中 → 结构化分级 → LLM → 事件链，
    每个信号处理后的提交边界不变。

    与旧 ``_process_pending_signals`` 的区别只有两点：**不自己取进程锁**、
    **不写 job 观测结论**。并发控制与观测交给调用方（兼容入口
    ``_process_pending_signals`` 或独立消费循环 ``scheduler.consume``），
    使采集与消费解耦、消费侧可按时间预算连续拉多批。
    """
    now = now_utc or datetime.now(UTC)
    processed = 0
    filtered = 0
    failed = 0
    with SessionLocal() as session:
        # 只取队首一小段候选：旧实现每轮全量加载（积压上万行），改为 20 秒一轮
        # 消费后会放大成常态开销。留 20 倍余量，用于跳过循环中判定为过期的 active。
        candidates = list(
            session.execute(
                pending_signal_candidate_select(now_utc=now).limit(batch * 20)
            )
        )
        signal_ids: list[tuple[int, str]] = []
        validity_updated = False
        for signal_id, source_code in candidates:
            signal = session.get(RawSignal, signal_id)
            if signal is None:
                continue
            if signal.validity_state == "active" and not is_raw_signal_effective(
                signal, now_utc=now
            ):
                expire_signal_if_due(signal, now_utc=now)
                validity_updated = True
                continue
            signal_ids.append((signal_id, source_code))
            if len(signal_ids) == batch:
                break
        if validity_updated:
            session.commit()
        for signal_id, source_code in signal_ids:
            signal = session.get(RawSignal, signal_id)
            if signal is None:
                continue
            # 处理前登记当前信号（独立短事务；写失败不影响业务）。
            runtime.record_pending_signal_started(signal.id, now=now)
            analysis = latest_reusable_analysis(session, signal.id)
            # 行情分支优先：已登记指标由确定性评估接管（命中即终态，触发即带结果
            # 继续处理链）；未命中则原样回落既有相关性/清单/分级链，顺序不变。
            if analysis is None:
                market_analysis, market_handled = _evaluate_market_signal(
                    session, signal, source_code, now_utc=now
                )
                if market_handled:
                    if market_analysis is None:
                        filtered += 1
                        continue
                    analysis = market_analysis
            if analysis is None and SIGNAL_RELEVANCE_FILTER_ENABLED:
                decision = assess_signal_relevance(
                    session, signal.title, signal.content
                )
                if not decision.relevant:
                    session.add(
                        AIAnalysisRecord(
                            signal_id=signal.id,
                            provider="deterministic-filter",
                            model="relevance-v1",
                            prompt_version="relevance-v1",
                            status="succeeded",
                            finished_at=datetime.now(UTC),
                            duration_ms=0,
                            result=None,
                            error=f"filtered: {decision.reason}",
                            needs_review=True,
                            review_reason=f"预过滤结果待复核：{decision.reason}",
                        )
                    )
                    _mark_signal_irrelevant(
                        signal,
                        filter_kind=RELEVANCE_FILTER_KIND,
                        reason=decision.reason,
                    )
                    session.commit()
                    filtered += 1
                    continue
            # 清单类信源（全量实体清单）：未命中任何供应商名则跳过 LLM
            # （清单集合来自 signal-filter 配置，TTL 缓存热更新）
            list_sources = load_filter_rules(session).list_sources
            if (
                analysis is None
                and
                source_code in list_sources
                and not _matches_any_supplier(
                    session, f"{signal.title} {signal.content}"
                )
            ):
                session.add(
                    AIAnalysisRecord(
                        signal_id=signal.id,
                        provider="deterministic-filter",
                        model="entity-list-v1",
                        prompt_version="entity-list-v1",
                        status="succeeded",
                        finished_at=datetime.now(UTC),
                        duration_ms=0,
                        result=None,
                        error="filtered: 清单类信号未命中任何供应商",
                        needs_review=True,
                        review_reason="实体清单未命中任何供应商，跳过 LLM",
                    )
                )
                _mark_signal_irrelevant(
                    signal,
                    filter_kind=ENTITY_LIST_FILTER_KIND,
                    reason="清单类信号未命中任何供应商",
                )
                session.commit()
                filtered += 1
                continue
            # 结构化宏观信号分级：commodity 涨跌幅低于阈值 → 免 LLM
            rules = load_filter_rules(session)
            grade_reason = (
                grade_structured_signal(
                    source_code, signal.title, signal.content, rules
                )
                if analysis is None
                else None
            )
            if grade_reason is not None:
                session.add(
                    AIAnalysisRecord(
                        signal_id=signal.id,
                        provider="deterministic-filter",
                        model="structured-grade-v1",
                        prompt_version="structured-grade-v1",
                        status="succeeded",
                        finished_at=datetime.now(UTC),
                        duration_ms=0,
                        result=None,
                        error=f"filtered: {grade_reason}",
                        needs_review=True,
                        review_reason=f"结构化分级跳过：{grade_reason}",
                    )
                )
                _mark_signal_irrelevant(
                    signal,
                    filter_kind=STRUCTURED_GRADE_FILTER_KIND,
                    reason=grade_reason,
                )
                session.commit()
                filtered += 1
                continue
            try:
                if analysis is None:
                    analysis = asyncio.run(analyze_raw_signal(session, signal))
                if analysis.status != "succeeded" or analysis.result is None:
                    continue
                process_analysis(session, signal, analysis, now_utc=now)
                processed += 1
            except Exception as exc:
                session.rollback()
                failed += 1
                logger.error("信号 %s 处理失败: %s", signal_id, exc)
    if filtered:
        logger.info("相关性预过滤跳过 %d 条不相关信号（未消耗 LLM）", filtered)
    return BatchResult(processed=processed, filtered=filtered, failed=failed)


def _process_pending_signals(
    limit: int | None = None, *, now_utc: datetime | None = None
) -> int:
    """兼容入口：进程锁 + 单批处理 + 观测，返回本批 processed 条数。

    limit 为 None 时使用 SIGNAL_ANALYZE_BATCH 环境变量（默认 20）。
    业务主体全部在 ``process_signal_batch``：可复用的成功分析之后先做
    **确定性行情评估**（已登记指标免 LLM，未触发写终态过滤记录，触发直接以
    确定性结果进入事件链），未命中才继续既有链路——相关性预过滤
    （SIGNAL_RELEVANCE_FILTER_ENABLED）、清单未命中、结构化分级，语义不变。

    采集侧不再调用本函数（采集与消费解耦）；保留它是为了手动触发、
    既有测试与旧调用点，行为与重构前一致。
    """
    if not _pending_signal_processing_lock.acquire(blocking=False):
        logger.info("已有待处理信号批次运行，跳过本次重复处理")
        return 0

    # 任务开始写观测（独立短事务；lock 未获得时保持零观测）。失败不影响业务。
    runtime.record_job_started(
        runtime.PENDING_SIGNALS_JOB_KEY,
        kind="pending_signal_processing",
        stage="processing_signal",
    )

    batch = SIGNAL_ANALYZE_BATCH if limit is None else limit
    try:
        result = process_signal_batch(batch, now_utc=now_utc)
    except Exception:
        # 业务事务因基础设施异常失败：得出失败结论后，用独立短事务写观测再抛出。
        runtime.record_job_result(
            runtime.PENDING_SIGNALS_JOB_KEY,
            succeeded=False,
            failed=1,
            error_code="pending_processing_failed",
        )
        raise
    else:
        # 业务结论成功后，用独立短事务写本轮 processed/filtered/failed。
        runtime.record_job_result(
            runtime.PENDING_SIGNALS_JOB_KEY,
            succeeded=True,
            processed=result.processed,
            filtered=result.filtered,
            failed=result.failed,
        )
        return result.processed
    finally:
        _pending_signal_processing_lock.release()


def _risk_processed_signal_ids() -> Select[tuple[int]]:
    return select(RiskEventSignal.signal_id)


def _terminal_filtered_signal_ids() -> Select[tuple[int]]:
    """终态过滤信号：成功但结果为空（SQL NULL 或 JSON null，含确定性预过滤）。

    historical：队列谓词自 0066 起改用 ``processing_state`` 表达处理阶段，不再依赖
    本子查询；保留它是给历史回填/审计口径做纵深对照，勿在新谓词里复活。
    """
    return select(AIAnalysisRecord.signal_id).where(
        AIAnalysisRecord.status == "succeeded",
        ~usable_analysis_result(),
    )


def pending_signal_candidate_filters(
    *, now_utc: datetime | None = None
) -> list[ColumnElement[bool]]:
    """当前分析队列候选信号的共享谓词（业务处理链与只读健康聚合复用同一份）。

    要求已 join ``DataSource``。以 ``processing_state='pending_classification'`` 表达
    「尚未处理」：已分析、已确定性过滤、行情未触发、分类失败的信号一律不再入队；
    生命周期维度只接受未定（NULL）或 active，并排除已过 valid_until 的行。停用信源
    下的信号同样排除。绝不复制会与业务漂移的第二份谓词。
    """
    now = now_utc or datetime.now(UTC)
    return [
        RawSignal.processing_state == "pending_classification",
        RawSignal.id.not_in(_risk_processed_signal_ids()),
        DataSource.enabled.is_(True),
        or_(RawSignal.validity_state.is_(None), RawSignal.validity_state == "active"),
        or_(
            RawSignal.validity_state.is_(None),
            RawSignal.valid_until.is_(None),
            RawSignal.valid_until > now,
        ),
    ]


def pending_signal_candidate_select(
    *, now_utc: datetime | None = None
) -> Select[tuple[int, str]]:
    """候选信号 ``(id, source_code)``，按消费顺序排序；不施加任何 batch limit。

    启用优先级时按 ``priority DESC, collected_at ASC``（高优先级先消费，同级
    先进先出）；关闭时保持纯 ``collected_at`` 升序的旧顺序。
    """
    statement = (
        select(RawSignal.id, DataSource.code)
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(*pending_signal_candidate_filters(now_utc=now_utc))
    )
    if SIGNAL_PRIORITY_ENABLED:
        return statement.order_by(RawSignal.priority.desc(), RawSignal.collected_at.asc())
    return statement.order_by(RawSignal.collected_at)


def pending_signal_candidate_id_select(
    *, now_utc: datetime | None = None
) -> Select[tuple[int]]:
    """候选信号 id 集合（健康聚合计数用）；同样不施加 batch limit。"""
    return (
        select(RawSignal.id.label("signal_id"))
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(*pending_signal_candidate_filters(now_utc=now_utc))
    )


def age_pending_signal_priorities(
    *, now_utc: datetime | None = None, limit: int = 5000
) -> int:
    """低优先级老化提权：等待超过阈值的最老候选信号优先 +5（上限 90）。

    只处理仍在候选队列里的信号（复用同一份候选谓词），按 ``collected_at`` 取
    最老 ``limit`` 条，单事务 ``UPDATE ... SET priority = LEAST(priority + 5, 90)``；
    返回实际更新行数。每轮只提一档，由周期任务反复执行逐步抬高，避免一轮内
    把积压全部抬到最高优先级、反而挤掉真正的新高优信号。
    """
    now = now_utc or datetime.now(UTC)
    if not SIGNAL_PRIORITY_ENABLED:
        return 0
    cutoff = now - timedelta(hours=SIGNAL_PRIORITY_AGING_HOURS)
    with SessionLocal() as session:
        oldest_ids = list(
            session.scalars(
                select(RawSignal.id)
                .where(
                    RawSignal.id.in_(pending_signal_candidate_id_select(now_utc=now)),
                    RawSignal.collected_at < cutoff,
                    RawSignal.priority < 90,
                )
                .order_by(RawSignal.collected_at)
                .limit(limit)
            )
        )
        if not oldest_ids:
            return 0
        result = session.execute(
            update(RawSignal)
            .where(RawSignal.id.in_(oldest_ids))
            .values(priority=func.least(RawSignal.priority + 5, 90))
        )
        session.commit()
        return int(result.rowcount or 0)


def age_pending_signal_priorities_job() -> RunResult:
    """Scheduler 注册用老化 wrapper：保留原 int 契约，同时返回脱敏摘要。"""
    updated = age_pending_signal_priorities()
    return RunResult(
        summary="优先级老化完成",
        metrics=[RunResultMetric(label="提升信号", value=max(0, updated))],
    )


def collect_job() -> RunResult | None:
    """定时采集：只入库，不消费。分析由独立消费循环（signal-consume）负责。"""
    logger.info("定时采集开始: %s", datetime.now(UTC).isoformat())
    try:
        summary = _collect_enabled_sources(only_without_schedule=True)
        logger.info("采集汇总: %s（新信号已入队，等待独立消费循环分析）", summary)
        created = sum(value for value in summary.values() if value > 0)
        failed = sum(1 for value in summary.values() if value < 0)
        return RunResult(
            summary="采集完成",
            metrics=[
                RunResultMetric(label="采集信源", value=len(summary)),
                RunResultMetric(label="新增信号", value=max(0, created)),
                RunResultMetric(label="失败信源", value=max(0, failed)),
            ],
            outputs=[RunResultMetric(label="新增信号", value=max(0, created))],
        )
    except Exception as exc:
        logger.exception("定时采集任务异常: %s", exc)
        return None


def create_research_task_job(task_type: str, *, now: datetime | None = None) -> int | None:
    """按日或按自然周幂等创建研究任务；不执行搜索、抓取或模型调用。"""
    topics = {"daily": RESEARCH_DAILY_TOPIC, "weekly": RESEARCH_WEEKLY_TOPIC}
    if task_type not in topics:
        raise ValueError(f"不支持的研究调度类型: {task_type}")
    topic = topics[task_type]
    if not topic or not RESEARCH_SCHEDULE_OWNER_USERNAME:
        logger.info("跳过%s研究任务：主题或归属管理员未配置", task_type)
        return None

    current = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    period = (
        current.date().isoformat()
        if task_type == "daily"
        else f"{current.isocalendar().year}-W{current.isocalendar().week:02d}"
    )
    idempotency_key = f"scheduled:{task_type}:{period}"
    with SessionLocal() as session:
        owner = session.scalar(
            select(User).where(
                User.username == RESEARCH_SCHEDULE_OWNER_USERNAME,
                User.status == "active",
                User.role.in_(("risk_admin", "platform_admin")),
            )
        )
        if owner is None:
            logger.error("跳过%s研究任务：归属管理员不存在、未启用或权限不足", task_type)
            return None
        existing = session.scalar(
            select(ResearchTask).where(
                ResearchTask.owner_user_id == owner.id,
                ResearchTask.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing.id
        task = create_task(
            session,
            owner_user_id=owner.id,
            task_type=task_type,
            topic=topic,
            supplier_scope=[],
            idempotency_key=idempotency_key,
        )
        write_audit(
            session,
            action="research_task_created",
            resource_type="research_task",
            resource_id=str(task.id),
            detail=f"task_type={task_type} trigger=scheduler",
        )
        session.commit()
        logger.info("已创建%s研究任务 %s，周期=%s", task_type, task.id, period)
        return task.id


def _merge_research_budget(budget_template: dict[str, object]) -> dict[str, object]:
    budget = dict(DEFAULT_RESEARCH_BUDGET)
    budget.update(budget_template)
    return budget


def _create_periodic_research_batch(
    session: Session,
    *,
    period_type: str,
    topic: str,
    budget_snapshot: dict[str, object],
    now: datetime,
    suppliers: list[Supplier] | None = None,
) -> int | None:
    """按周期快照启用供应商并扇出同一研究批次流程。"""
    if period_type not in {"weekly", "monthly"}:
        raise ValueError("不支持的周期研究批次类型")
    current = now.astimezone(SHANGHAI_TZ)
    if period_type == "monthly":
        period_key = f"{current.year:04d}-{current.month:02d}"
        period_start = current.date().replace(day=1)
        period_end = current.date().replace(day=monthrange(current.year, current.month)[1])
    else:
        iso = current.isocalendar()
        period_key = f"{iso.year}-W{iso.week:02d}"
        period_start = (current - timedelta(days=current.weekday())).date()
        period_end = period_start + timedelta(days=6)
    owner = session.scalar(
        select(User).where(
            User.username == RESEARCH_SCHEDULE_OWNER_USERNAME,
            User.status == "active",
            User.role.in_(("risk_admin", "platform_admin")),
        )
    )
    if owner is None:
        logger.error("跳过%s批次：归属管理员不存在、未启用或权限不足", period_type)
        return None
    existing = session.scalar(
        select(ResearchBatch).where(
            ResearchBatch.owner_user_id == owner.id,
            ResearchBatch.period_type == period_type,
            ResearchBatch.period_key == period_key,
        )
    )
    if existing is not None:
        return existing.id
    if suppliers is None:
        suppliers = (
            select_monthly_suppliers(session)
            if period_type == "monthly"
            else list(
                session.scalars(
                    select(Supplier).where(Supplier.enabled.is_(True)).order_by(Supplier.id)
                )
            )
        )
    if not suppliers:
        logger.info("跳过%s批次：没有启用供应商", period_type)
        return None
    supplier_snapshot = [
        {
            "supplier_id": supplier.id,
            "supplier_code": supplier.supplier_code,
            "legal_name": supplier.legal_name,
        }
        for supplier in suppliers
    ]
    blocking_batch = session.scalar(
        select(ResearchBatch)
        .where(
            ResearchBatch.owner_user_id == owner.id,
            ResearchBatch.period_type == period_type,
            ResearchBatch.period_end < period_start,
            ResearchBatch.status.in_(("queued", "running")),
        )
        .order_by(ResearchBatch.period_end.desc(), ResearchBatch.id.desc())
    )
    if blocking_batch is not None:
        batch = ResearchBatch(
            owner_user_id=owner.id,
            period_type=period_type,
            period_key=period_key,
            period_start=period_start,
            period_end=period_end,
            topic=topic,
            supplier_snapshot=supplier_snapshot,
            supplier_count=len(supplier_snapshot),
            budget_snapshot=dict(budget_snapshot),
            status="capacity_blocked",
            error=(
                f"前一{period_type}批次 {blocking_batch.id} 尚未完成，等待单 Worker 串行恢复"
            ),
            graph_version=(
                "research-graph-v2" if RESEARCH_ORCHESTRATOR == "langgraph" else None
            ),
        )
        session.add(batch)
        session.flush()
        write_audit(
            session,
            action="research_batch_capacity_blocked",
            actor_user_id=owner.id,
            resource_type="research_batch",
            resource_id=str(batch.id),
            detail=f"period_type={period_type};blocked_by_batch={blocking_batch.id}",
        )
        session.commit()
        logger.warning(
            "%s批次 %s 因前序批次 %s 未完成而容量阻塞",
            period_type,
            batch.id,
            blocking_batch.id,
        )
        return batch.id
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type=period_type,
        period_key=period_key,
        period_start=period_start,
        period_end=period_end,
        topic=topic,
        supplier_snapshot=supplier_snapshot,
        supplier_count=len(supplier_snapshot),
        queued_count=len(suppliers),
        budget_snapshot=dict(budget_snapshot),
        graph_version=(
            "research-graph-v2" if RESEARCH_ORCHESTRATOR == "langgraph" else None
        ),
    )
    session.add(batch)
    session.flush()
    for supplier in suppliers:
        create_task(
            session,
            owner_user_id=owner.id,
            task_type=period_type,
            topic=topic,
            supplier_scope=[supplier.id],
            idempotency_key=f"scheduled:{period_type}:{period_key}:{supplier.id}",
            budget_snapshot=dict(budget_snapshot),
            batch_id=batch.id,
        )
    write_audit(
        session,
        action="research_batch_created",
        actor_user_id=owner.id,
        resource_type="research_batch",
        resource_id=str(batch.id),
        detail=f"period_type={period_type};period_key={period_key};supplier_count={len(suppliers)}",
    )
    session.commit()
    logger.info(
        "已创建%s批次 %s，供应商数=%d，周期=%s",
        period_type,
        batch.id,
        len(suppliers),
        period_key,
    )
    return batch.id


def _capacity_blocked_batch_preflight(
    session: Session, batch: ResearchBatch, *, now: datetime
) -> bool:
    if batch.period_type == "monthly":
        if not RESEARCH_MONTHLY_ENABLED or not RESEARCH_MONTHLY_TOPIC:
            return False
        preflight = monthly_schedule_preflight(
            session,
            cron_expression=RESEARCH_MONTHLY_CRON,
            topic_template=RESEARCH_MONTHLY_TOPIC,
            budget_template=dict(batch.budget_snapshot),
            supplier_count=batch.supplier_count,
            now=now,
        )
        return preflight.can_enable
    config = get_schedule_config(session, schedule_type="weekly")
    if config is None or not config.enabled:
        return False
    preflight = weekly_schedule_preflight(
        session,
        cron_expression=config.cron_expression,
        topic_template=config.topic_template,
        budget_template=config.budget_template,
        approved_monthly_quota=config.approved_monthly_quota,
        now=now,
    )
    return preflight.can_enable


def recover_capacity_blocked_research_batches_job(
    *, now: datetime | None = None
) -> int:
    """前序批次完成后，按原快照恢复容量阻塞批次。"""
    current = now or datetime.now(SHANGHAI_TZ)
    activated = 0
    with SessionLocal() as session:
        blocked_batches = list(
            session.scalars(
                select(ResearchBatch)
                .where(ResearchBatch.status == "capacity_blocked")
                .order_by(ResearchBatch.period_start, ResearchBatch.id)
            )
        )
        for batch in blocked_batches:
            blocking_batch = session.scalar(
                select(ResearchBatch)
                .where(
                    ResearchBatch.owner_user_id == batch.owner_user_id,
                    ResearchBatch.period_type == batch.period_type,
                    ResearchBatch.period_end < batch.period_start,
                    ResearchBatch.status.in_(("queued", "running")),
                )
                .order_by(ResearchBatch.period_end.desc(), ResearchBatch.id.desc())
            )
            if blocking_batch is not None or not _capacity_blocked_batch_preflight(
                session, batch, now=current
            ):
                continue
            for item in batch.supplier_snapshot:
                supplier_id = item.get("supplier_id")
                if not isinstance(supplier_id, int):
                    continue
                create_task(
                    session,
                    owner_user_id=batch.owner_user_id,
                    task_type=batch.period_type,
                    topic=batch.topic,
                    supplier_scope=[supplier_id],
                    idempotency_key=(
                        f"scheduled:{batch.period_type}:{batch.period_key}:{supplier_id}"
                    ),
                    budget_snapshot=dict(batch.budget_snapshot),
                    batch_id=batch.id,
                )
            batch.status = "queued"
            batch.queued_count = len(batch.supplier_snapshot)
            batch.error = None
            write_audit(
                session,
                action="research_batch_capacity_released",
                actor_user_id=batch.owner_user_id,
                resource_type="research_batch",
                resource_id=str(batch.id),
                detail=f"period_type={batch.period_type};supplier_count={len(batch.supplier_snapshot)}",
            )
            session.commit()
            activated += 1
    if activated:
        logger.info("已恢复容量阻塞研究批次 %d 个", activated)
    return activated


def create_monthly_research_batch_job(*, now: datetime | None = None) -> int | None:
    """按自然月快照月报范围内的供应商并扇出 monthly 子任务。"""
    if not RESEARCH_MONTHLY_ENABLED:
        logger.info("跳过月报批次：RESEARCH_MONTHLY_ENABLED=false")
        return None
    if not RESEARCH_MONTHLY_TOPIC or not RESEARCH_SCHEDULE_OWNER_USERNAME:
        logger.info("跳过月报批次：主题或归属管理员未配置")
        return None
    current = now or datetime.now(SHANGHAI_TZ)
    with SessionLocal() as session:
        suppliers = select_monthly_suppliers(session)
        preflight = monthly_schedule_preflight(
            session,
            cron_expression=RESEARCH_MONTHLY_CRON,
            topic_template=RESEARCH_MONTHLY_TOPIC,
            budget_template=DEFAULT_MONTHLY_RESEARCH_BUDGET,
            supplier_count=len(suppliers),
            now=current,
        )
        if not preflight.can_enable:
            logger.warning("跳过月报批次：%s", preflight.block_reason)
            return None
        return _create_periodic_research_batch(
            session,
            period_type="monthly",
            topic=RESEARCH_MONTHLY_TOPIC,
            budget_snapshot=dict(DEFAULT_MONTHLY_RESEARCH_BUDGET),
            now=current,
            suppliers=suppliers,
        )


def create_weekly_research_batch_job(*, now: datetime | None = None) -> int | None:
    """运行时开关打开且预检通过时创建 weekly 全供应商批次。"""
    current = now or datetime.now(SHANGHAI_TZ)
    with SessionLocal() as session:
        config = get_schedule_config(session, schedule_type="weekly")
        if config is None or not config.enabled:
            logger.info("跳过周报批次：运行时开关关闭")
            return None
        preflight = weekly_schedule_preflight(
            session,
            cron_expression=config.cron_expression,
            topic_template=config.topic_template,
            budget_template=config.budget_template,
            approved_monthly_quota=config.approved_monthly_quota,
            now=current,
        )
        if not preflight.can_enable:
            logger.warning("跳过周报批次：%s", preflight.block_reason)
            return None
        if not RESEARCH_SCHEDULE_OWNER_USERNAME:
            logger.warning("跳过周报批次：归属管理员未配置")
            return None
        topic = config.topic_template.replace("{period}", preflight.period_key)
        return _create_periodic_research_batch(
            session,
            period_type="weekly",
            topic=topic,
            budget_snapshot=_merge_research_budget(config.budget_template),
            now=current,
        )


def intelligence_report_job() -> RunResult | None:
    """为政策/行业维度的风险事件批量生成情报报告（简要报告 + 风险提醒建议）。

    与提醒链路完全解耦：只读事件，不做供应商匹配、不评分、不推送。批次大小由
    INTELLIGENCE_REPORT_BATCH 控制，单条失败不终止整批（错误由服务层落 failed 行）。
    """
    try:
        generated = generate_pending_intelligence_reports()
        if generated:
            logger.info("情报报告生成完成: %d 条", generated)
        return RunResult(
            summary="情报报告生成完成",
            metrics=[RunResultMetric(label="报告数", value=max(0, generated))],
        )
    except Exception as exc:
        logger.exception("情报报告任务异常: %s", exc)
        return None


def expire_job() -> RunResult | None:
    with SessionLocal() as session:
        try:
            expired = expire_alerts(session)
            session.commit()
            if expired:
                logger.info("已失效提醒 %d 条", expired)
            return RunResult(
                summary="提醒失效完成",
                metrics=[RunResultMetric(label="过期提醒", value=max(0, expired))],
            )
        except Exception as exc:
            session.rollback()
            logger.exception("提醒失效任务异常: %s", exc)
            return None


def cleanup_job() -> RunResult | None:
    with SessionLocal() as session:
        try:
            now = datetime.now(UTC)
            reconciled_tool_runs = reconcile_stale_tool_runs(
                session,
                stale_before=now - timedelta(seconds=RESEARCH_TOOL_RUN_STALE_SECONDS),
                now=now,
            )
            result = cleanup_retention(session)
            logger.info(
                "保留清理完成: 遗留工具对账=%d 失效提醒=%d 事件=%d 信号=%d 分析记录=%d 运行记录=%d",
                reconciled_tool_runs,
                result.expired_alerts,
                result.deleted_events,
                result.deleted_signals,
                result.deleted_analysis,
                result.deleted_runs,
            )
            return RunResult(
                summary="保留清理完成",
                metrics=[
                    RunResultMetric(
                        label="过期提醒", value=max(0, result.expired_alerts)
                    ),
                    RunResultMetric(
                        label="删除事件", value=max(0, result.deleted_events)
                    ),
                    RunResultMetric(
                        label="删除信号", value=max(0, result.deleted_signals)
                    ),
                    RunResultMetric(
                        label="删除分析", value=max(0, result.deleted_analysis)
                    ),
                    RunResultMetric(
                        label="删除采集运行", value=max(0, result.deleted_runs)
                    ),
                    RunResultMetric(
                        label="删除调度历史",
                        value=max(0, result.deleted_scheduler_runs),
                    ),
                ],
            )
        except Exception as exc:
            session.rollback()
            logger.exception("保留清理任务异常: %s", exc)
            return None
