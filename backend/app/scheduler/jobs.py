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
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.service import analyze_raw_signal
from app.auth.models import User
from app.auth.security import write_audit
from app.config import (
    RESEARCH_DAILY_TOPIC,
    RESEARCH_SCHEDULE_OWNER_USERNAME,
    RESEARCH_WEEKLY_TOPIC,
    SIGNAL_ANALYZE_BATCH,
    SIGNAL_RELEVANCE_FILTER_ENABLED,
)
from app.database import SessionLocal
from app.research.models import ResearchTask
from app.research.service import create_task
from app.risks.service import expire_alerts, process_analysis
from app.scheduler.retention import cleanup_retention
from app.signals.models import DataSource, RawSignal
from app.signals.relevance import assess_signal_relevance
from app.signals.router import build_pull_adapter
from app.signals.service import CollectionFailed, collect_source

logger = logging.getLogger("scheduler")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


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


def _process_pending_signals(limit: int | None = None) -> int:
    """对尚无成功 AI 解析的信号执行解析与全链路处理，返回处理条数。

    limit 为 None 时使用 SIGNAL_ANALYZE_BATCH 环境变量（默认 20）。
    调用 LLM 前先做确定性相关性预过滤（SIGNAL_RELEVANCE_FILTER_ENABLED），
    明确不相关的信号跳过 LLM 并写入 filtered 解析记录，避免重复取出。
    """
    batch = SIGNAL_ANALYZE_BATCH if limit is None else limit
    processed = 0
    filtered = 0
    with SessionLocal() as session:
        signal_ids = list(
            session.scalars(
                select(RawSignal.id)
                .where(RawSignal.id.not_in(_succeeded_signal_ids(session)))
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
            result = cleanup_retention(session)
            logger.info(
                "保留清理完成: 失效提醒=%d 事件=%d 信号=%d 分析记录=%d 运行记录=%d",
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
