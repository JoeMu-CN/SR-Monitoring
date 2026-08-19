"""独立 Scheduler 进程（技术方案 5.1：Web 与调度分离）。

定时任务：
- collect_job：定时采集所有启用的拉取式数据源（nmc-weather），并处理新信号
  （AI 解析 → 事件归并 → 匹配 → 评分 → 提醒）。
- expire_job：定期将超过 expires_at 的提醒标记为 expired。
- cleanup_job：每日数据保留清理（90 天 / 30 天轮转）。

启动方式：python -m app.scheduler.main
"""

from __future__ import annotations

import asyncio
import logging
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.service import analyze_raw_signal
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
    SIGNAL_RELEVANCE_FILTER_ENABLED,
)
from app.database import SessionLocal
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
from app.risks.service import expire_alerts, process_analysis
from app.scheduler.retention import cleanup_retention
from app.signals.models import DataSource, RawSignal
from app.signals.relevance import assess_signal_relevance
from app.signals.router import build_pull_adapter
from app.signals.service import CollectionFailed, collect_source
from app.suppliers.models import Supplier

logger = logging.getLogger("scheduler")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
# ponytail: 当前生产仅单 Scheduler 进程；扩为多副本前改用数据库级原子领取或 advisory lock。
_pending_signal_processing_lock = Lock()


def _collect_enabled_sources(
    *, source_ids: list[int] | None = None, only_without_schedule: bool = False
) -> dict[str, int]:
    """采集启用的拉取式数据源，返回 ``{source_code: created_count}``。"""
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
                logger.info("跳过数据源 %s（非拉取式）: %s", source.code, exc)
                continue
            try:
                run = collect_source(session, source, pull_adapter)
            except CollectionFailed as exc:
                logger.error("数据源 %s 采集失败: %s", source.code, exc)
                summary[source.code] = -1
                continue
            summary[source.code] = run.created_count
            logger.info(
                "数据源 %s 采集完成: fetched=%d created=%d dup=%d",
                source.code,
                run.fetched_count,
                run.created_count,
                run.duplicate_count,
            )
    return summary


def collect_source_job(source_id: int) -> None:
    """按数据源自己的 cron 触发一次采集，并处理本次新增信号。"""
    try:
        summary = _collect_enabled_sources(source_ids=[source_id])
        if summary:
            _process_pending_signals()
            logger.info("数据源独立调度完成: %s", summary)
    except Exception as exc:
        logger.exception("数据源 %s 独立调度异常: %s", source_id, exc)


def collect_tyc_for_suppliers_job() -> None:
    """每日批量核查启用的供应商：调用天眼查 MCP 并把结果写入信号池。

    只对 suppliers.enabled=true 的供应商执行；受天眼查每日/每月额度控制
    （get_tyc_usage.allowed），额度耗尽即停止。结果写入 raw_signals，
    随后交由既有 _process_pending_signals 分析链生成 P1-P4 提醒。
    """
    try:
        import asyncio as _asyncio

        from app.agent.budget import get_tyc_usage
        from app.agent.supplier_tyc import upsert_supplier_tyc_signal
        from app.agent.tyc_gateway import build_tyc_gateway

        with SessionLocal() as session:
            usage = get_tyc_usage(session)
            if not usage.enabled:
                logger.warning("天眼查未启用，跳过供应商批量核查")
                return
            if not usage.allowed:
                logger.warning(
                    "天眼查额度不足（今日 %d/%d，本月 %d/%d），跳过供应商批量核查",
                    usage.daily_used, usage.daily_limit,
                    usage.monthly_used, usage.monthly_limit,
                )
                return
            suppliers = list(
                session.scalars(
                    select(Supplier)
                    .where(Supplier.enabled.is_(True))
                    .order_by(Supplier.supplier_code)
                )
            )
        if not suppliers:
            logger.info("无启用供应商，跳过天眼查批量核查")
            return
        gateway = build_tyc_gateway()
        created = 0
        skipped = 0
        failed = 0
        for supplier in suppliers:
            with SessionLocal() as session:
                if not get_tyc_usage(session).allowed:
                    logger.warning("天眼查额度耗尽，提前停止供应商批量核查")
                    break
                try:
                    result = _asyncio.run(gateway.verify(supplier.legal_name))
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning("天眼查核查 %s 失败: %s", supplier.legal_name, exc)
                    continue
                if result.get("status") != "success":
                    skipped += 1
                    continue
                title = f"天眼查核查：{supplier.legal_name}"
                content = _format_tyc_content(result)
                _, is_created = upsert_supplier_tyc_signal(
                    session,
                    supplier=supplier,
                    title=title,
                    content=content,
                    url=None,
                    raw_payload=result,
                )
                if is_created:
                    created += 1
                session.commit()
        logger.info(
            "天眼查批量核查完成: 供应商=%d 新增信号=%d 跳过=%d 失败=%d",
            len(suppliers), created, skipped, failed,
        )
        if created:
            _process_pending_signals()
    except Exception as exc:
        logger.exception("天眼查批量核查异常: %s", exc)


def _format_tyc_content(result: dict[str, object]) -> str:
    """把天眼查 verify 结果转成信号正文（含可回溯字段）。"""
    parts = [f"企业：{result.get('company_name', '')}"]
    if result.get("credit_code"):
        parts.append(f"统一社会信用代码：{result['credit_code']}")
    if result.get("reg_status"):
        parts.append(f"登记状态：{result['reg_status']}")
    candidates = result.get("candidates") or []
    if isinstance(candidates, list):
        for idx, cand in enumerate(candidates[:3], start=1):
            if isinstance(cand, dict) and cand.get("name"):
                parts.append(f"候选{idx}：{cand.get('name')}")
    return "；".join(parts)


def _process_pending_signals(limit: int | None = None) -> int:
    """对尚无成功 AI 解析的信号执行解析与全链路处理，返回处理条数。

    limit 为 None 时使用 SIGNAL_ANALYZE_BATCH 环境变量（默认 20）。
    调用 LLM 前先做确定性相关性预过滤（SIGNAL_RELEVANCE_FILTER_ENABLED），
    明确不相关的信号跳过 LLM 并写入 filtered 解析记录，避免重复取出。
    """
    if not _pending_signal_processing_lock.acquire(blocking=False):
        logger.info("已有待处理信号批次运行，跳过本次重复处理")
        return 0

    batch = SIGNAL_ANALYZE_BATCH if limit is None else limit
    processed = 0
    filtered = 0
    try:
        with SessionLocal() as session:
            signal_ids = list(
                session.scalars(
                    select(RawSignal.id)
                    .join(DataSource, DataSource.id == RawSignal.source_id)
                    .where(RawSignal.id.not_in(_succeeded_signal_ids(session)))
                    .where(DataSource.enabled.is_(True))
                    .order_by(RawSignal.collected_at)
                    .limit(batch)
                )
            )
            for signal_id in signal_ids:
                signal = session.get(RawSignal, signal_id)
                if signal is None:
                    continue
                if SIGNAL_RELEVANCE_FILTER_ENABLED:
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
                        session.commit()
                        filtered += 1
                        continue
                try:
                    analysis = asyncio.run(analyze_raw_signal(session, signal))
                    if analysis.status != "succeeded" or analysis.result is None:
                        continue
                    process_analysis(session, signal, analysis)
                    session.commit()
                    processed += 1
                except Exception as exc:
                    session.rollback()
                    logger.error("信号 %s 处理失败: %s", signal_id, exc)
        if filtered:
            logger.info("相关性预过滤跳过 %d 条不相关信号（未消耗 LLM）", filtered)
        return processed
    finally:
        _pending_signal_processing_lock.release()


def _succeeded_signal_ids(session: Session) -> Select[tuple[int]]:
    from app.ai.models import AIAnalysisRecord

    return select(AIAnalysisRecord.signal_id).where(
        AIAnalysisRecord.status == "succeeded"
    )


def collect_job() -> None:
    logger.info("定时采集开始: %s", datetime.now(UTC).isoformat())
    try:
        summary = _collect_enabled_sources(only_without_schedule=True)
        logger.info("采集汇总: %s", summary)
        processed = _process_pending_signals()
        logger.info("本次处理新信号 %d 条", processed)
    except Exception as exc:
        logger.exception("定时采集任务异常: %s", exc)


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


def expire_job() -> None:
    with SessionLocal() as session:
        try:
            expired = expire_alerts(session)
            session.commit()
            if expired:
                logger.info("已失效提醒 %d 条", expired)
        except Exception as exc:
            session.rollback()
            logger.exception("提醒失效任务异常: %s", exc)


def cleanup_job() -> None:
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
        except Exception as exc:
            session.rollback()
            logger.exception("保留清理任务异常: %s", exc)


def _cron_to_apscheduler(expr: str) -> dict[str, str]:
    """将 '分 时 日 月 周' 5 段 cron 拆成 APScheduler 参数。"""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"非法 cron 表达式: {expr}")
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }
