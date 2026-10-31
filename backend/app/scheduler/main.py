"""Scheduler 进程入口。

启动：python -m app.scheduler.main
依赖：PostgreSQL 已迁移（alembic upgrade head 由 compose 启动命令执行）。
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import (
    NOTIFY_MONTHLY_REPORT_CRON,
    RESEARCH_DAILY_CRON,
    RESEARCH_DAILY_TOPIC,
    RESEARCH_MONTHLY_CRON,
    RESEARCH_MONTHLY_ENABLED,
    RESEARCH_MONTHLY_TOPIC,
    RESEARCH_SCHEDULE_OWNER_USERNAME,
    RESEARCH_TRACK_ENABLED,
    SCHEDULER_CLEANUP_CRON,
    SCHEDULER_COLLECT_CRON,
    SCHEDULER_EXPIRE_CRON,
    SCHEDULER_INTELLIGENCE_CRON,
    SIGNAL_CONSUME_INTERVAL_SECONDS,
    get_notification_settings,
)
from app.database import SessionLocal
from app.notification.monthly_report import monthly_report_job
from app.notification.service import notify_job
from app.research.schedule import get_schedule_config, weekly_schedule_preflight
from app.scheduler.consume import consume_pending_signals_job_with_result
from app.scheduler.events import attach_observability_listener
from app.scheduler.health_rules import cron_to_apscheduler
from app.scheduler.jobs import (
    TYC_SHARD_CRONS,
    age_pending_signal_priorities_job,
    cleanup_job,
    collect_job,
    collect_source_job,
    collect_tyc_shard_job,
    create_monthly_research_batch_job,
    create_research_task_job,
    create_weekly_research_batch_job,
    intelligence_report_job,
    recover_capacity_blocked_research_batches_job,
)
from app.scheduler.log_tail import scheduler_log_path
from app.scheduler.runtime import (
    HEARTBEAT_INTERVAL_SECONDS,
    record_heartbeat,
)
from app.scheduler.validity_job import risk_validity_job
from app.signals.models import DataSource
from app.signals.service import (
    COLLECTION_RUN_STALE_SECONDS,
    finalize_stale_collection_runs,
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
)
logger = logging.getLogger("scheduler.main")

# 文件日志句柄名：用于幂等判断，避免重复调用时挂多个 handler。
FILE_LOG_HANDLER_NAME = "scheduler-log-file"
FILE_LOG_MAX_BYTES = 10 * 1024 * 1024
FILE_LOG_BACKUP_COUNT = 5


def _configure_file_logging() -> None:
    """将 scheduler 日志同时写入共享卷中的滚动文件。

    由 Web API 的 ``/api/v1/system/scheduler-logs`` 只读 tail；目录不可写或
    挂载缺失时只告警，绝不影响调度器启动。仅在 ``main()`` 中调用，导入与
    测试不会创建日志文件。
    """
    path = scheduler_log_path()
    root = logging.getLogger()
    if any(handler.name == FILE_LOG_HANDLER_NAME for handler in root.handlers):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=FILE_LOG_MAX_BYTES,
            backupCount=FILE_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Scheduler 文件日志初始化失败，仅保留 stdout 输出: %s", path, exc_info=True)
        return
    handler.set_name(FILE_LOG_HANDLER_NAME)
    handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


def _trigger(expr: str) -> CronTrigger:
    return CronTrigger(**cron_to_apscheduler(expr), timezone="Asia/Shanghai")


# 天眼查周度分片（D8）：固定 2 片，周日跑 shard 0、周一跑 shard 1。
# 权威 cadence 常量与 ``collect_tyc_shard_job`` 同源（见 jobs.py），健康聚合共用。
TYC_SHARD_JOB_IDS: tuple[str, str] = ("tyc-shard-0", "tyc-shard-1")


def _register_tyc_shard_jobs(scheduler: BlockingScheduler) -> None:
    """幂等注册两个连续日的天眼查分片 job（触发日/时区/分片参数可测试）。"""
    pairs = zip(TYC_SHARD_JOB_IDS, TYC_SHARD_CRONS, strict=True)
    for shard_index, (job_id, cron) in enumerate(pairs):
        trigger = _trigger(cron)
        existing = scheduler.get_job(job_id)
        if existing is None:
            scheduler.add_job(
                collect_tyc_shard_job,
                trigger,
                args=[shard_index],
                id=job_id,
                name=f"天眼查供应商分片核查（shard {shard_index}）",
            )
        elif str(existing.trigger) != str(trigger):
            scheduler.reschedule_job(job_id, trigger=trigger)


def _finalize_stale_collection_runs_on_startup() -> int:
    """Scheduler 进程启动时收尾上次异常退出遗留的 running 采集运行。

    仅在启动、注册周期任务之前执行一次；正常采集循环不再扫描或改写 running
    记录，避免把仍在执行的长任务误判为陈旧（活动网络采集不持行锁，无法从
    数据库推断外部是否仍在运行）。当前部署为单 Scheduler 进程。使用独立
    SessionLocal 短事务，只收尾超过 ``COLLECTION_RUN_STALE_SECONDS``
    （默认 30 分钟）的记录；恢复失败只记日志、不阻塞启动。
    """
    now = datetime.now(UTC)
    try:
        with SessionLocal() as session:
            finalized = finalize_stale_collection_runs(
                session,
                stale_before=now - timedelta(seconds=COLLECTION_RUN_STALE_SECONDS),
                now=now,
            )
    except SQLAlchemyError:
        logger.exception("Scheduler 启动收尾遗留采集运行失败，跳过启动恢复")
        return 0
    if finalized:
        logger.warning("Scheduler 启动收尾遗留 running 采集运行 %d 条", finalized)
    else:
        logger.info("Scheduler 启动检查：没有超过阈值的遗留 running 采集运行")
    return finalized


def _run_risk_validity_catchup_on_startup() -> None:
    """Scheduler 启动补跑一次风险有效期物化。

    有效期的定时物化按整点 cron 触发；进程在整点前重启会错过那一轮，出现
    「统计已按实时判活排除，但提醒 status 仍是 current」的窗口。启动时用独立
    SessionLocal 短事务补跑一次，与周期任务共用同一幂等实现；任何异常只记日志，
    绝不阻塞调度器启动（数据库尚未迁移完成时同样安全跳过）。
    """
    try:
        result = risk_validity_job()
    except Exception:  # noqa: BLE001 - 启动补跑失败不得阻塞启动
        logger.exception("Scheduler 启动补跑风险有效期物化失败，继续启动")
        return
    if result is None:
        logger.warning("Scheduler 启动补跑风险有效期物化未完成，等待下一轮定时任务")
        return
    logger.info("Scheduler 启动补跑风险有效期物化完成")


def _register_source_jobs(scheduler: BlockingScheduler) -> None:
    """刷新信息源控制台中的独立 cron 任务。"""
    with SessionLocal() as session:
        sources = list(
            session.scalars(
                select(DataSource)
                .where(
                    DataSource.enabled.is_(True),
                    DataSource.schedule.is_not(None),
                    DataSource.adapter_status.in_(("builtin", "published")),
                )
                .order_by(DataSource.id)
            )
        )
    desired_job_ids = {f"source-{source.id}" for source in sources}
    for job in scheduler.get_jobs():
        if job.id.startswith("source-") and job.id not in desired_job_ids:
            scheduler.remove_job(job.id)
    for source in sources:
        assert source.schedule is not None
        try:
            job_id = f"source-{source.id}"
            trigger = _trigger(source.schedule)
            existing = scheduler.get_job(job_id)
            if existing is None:
                scheduler.add_job(
                    collect_source_job,
                    trigger,
                    args=[source.id],
                    id=job_id,
                    name=f"采集信息源 {source.code}",
                )
            elif str(existing.trigger) != str(trigger):
                scheduler.reschedule_job(job_id, trigger=trigger)
        except ValueError as exc:
            logger.error("跳过非法信息源调度周期 %s=%s: %s", source.code, source.schedule, exc)


def _register_weekly_research_job(scheduler: BlockingScheduler) -> None:
    """按数据库运行时开关动态注册或移除周报批次 Job。"""
    job_id = "research-weekly"
    existing = scheduler.get_job(job_id)
    if not RESEARCH_TRACK_ENABLED:
        if existing is not None:
            scheduler.remove_job(job_id)
        return
    with SessionLocal() as session:
        config = get_schedule_config(session, schedule_type="weekly")
        if config is None or not config.enabled:
            if existing is not None:
                scheduler.remove_job(job_id)
            return
        try:
            preflight = weekly_schedule_preflight(
                session,
                cron_expression=config.cron_expression,
                topic_template=config.topic_template,
                budget_template=config.budget_template,
                approved_monthly_quota=config.approved_monthly_quota,
            )
        except ValueError as exc:
            logger.error("周报配置无效，暂不注册：%s", exc)
            if existing is not None:
                scheduler.remove_job(job_id)
            return
    if not preflight.can_enable or not RESEARCH_SCHEDULE_OWNER_USERNAME:
        logger.warning(
            "周报未注册：%s",
            preflight.block_reason or "归属管理员未配置",
        )
        if existing is not None:
            scheduler.remove_job(job_id)
        return
    try:
        trigger = _trigger(config.cron_expression)
    except ValueError as exc:
        logger.error("周报 cron 无效，暂不注册：%s", exc)
        if existing is not None:
            scheduler.remove_job(job_id)
        return
    if existing is None:
        scheduler.add_job(
            create_weekly_research_batch_job,
            trigger,
            id=job_id,
            name="创建全供应商周报批次",
        )
    elif str(existing.trigger) != str(trigger):
        scheduler.reschedule_job(job_id, trigger=trigger)


def _register_signal_consumption(scheduler: BlockingScheduler) -> None:
    """注册独立信号消费循环与优先级老化 job（消费使用专用单线程执行器）。

    采集与消费彻底解耦：消费按 ``SIGNAL_CONSUME_INTERVAL_SECONDS`` 周期拉队列，
    单轮内按时间预算连续消费多批；``max_instances=1`` + ``coalesce`` 保证同一
    Scheduler 进程不会堆叠消费轮次，跨进程/副本互斥由 advisory lock 兜底。
    """
    # APScheduler 3.x 只接受其 BaseExecutor（内部封装 concurrent.futures 线程池），
    # 且 add_executor(executor, alias=...) 的别名必须用关键字参数。
    scheduler.add_executor(ThreadPoolExecutor(max_workers=1), alias="signal_consume")
    scheduler.add_job(
        consume_pending_signals_job_with_result,
        "interval",
        seconds=SIGNAL_CONSUME_INTERVAL_SECONDS,
        executor="signal_consume",
        max_instances=1,
        coalesce=True,
        id="signal-consume",
        name="待分析信号消费",
        replace_existing=True,
    )
    # 低优先级老化：等待过久的信号逐步提权，避免被高优噪声永久饿死。
    scheduler.add_job(
        age_pending_signal_priorities_job,
        "interval",
        hours=1,
        id="signal-priority-aging",
        name="待分析信号优先级老化",
        replace_existing=True,
    )


def main() -> None:
    _configure_file_logging()
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    # 排期快照与执行历史监听：必须在 scheduler.start() 前注册，使
    # SCHEDULER_STARTED 后立即落真实 next_run_time，并随 JOB_ADDED /
    # MODIFIED / REMOVED / SUBMITTED 持续同步；监听器异常自身隔离。
    attach_observability_listener(scheduler)
    # 启动恢复：仅本进程启动时收尾一次遗留 running 采集运行，然后才注册周期
    # 任务；正常采集循环不再扫描 running，避免误伤仍在执行的长任务。
    _finalize_stale_collection_runs_on_startup()
    # 启动补跑一次有效期物化：避免重启错过整点导致统计与 status 口径短暂不一致。
    _run_risk_validity_catchup_on_startup()
    _register_signal_consumption(scheduler)
    scheduler.add_job(
        collect_job, _trigger(SCHEDULER_COLLECT_CRON), id="collect", name="定时采集与处理"
    )
    # 供应商主体维度：周度两天分片多维度天眼查核查（周日 shard0、周一 shard1）；
    # 额度由信息源控制台配置，结果落信号池。
    _register_tyc_shard_jobs(scheduler)
    record_heartbeat()
    scheduler.add_job(
        record_heartbeat,
        "interval",
        seconds=HEARTBEAT_INTERVAL_SECONDS,
        id="runtime-heartbeat",
        name="持久化调度器心跳",
    )
    # 独立信号消费循环与优先级老化已在 _register_signal_consumption 注册（见上）。
    _register_source_jobs(scheduler)
    _register_weekly_research_job(scheduler)
    scheduler.add_job(
        _register_source_jobs,
        "interval",
        minutes=1,
        args=[scheduler],
        id="registry-refresh",
        name="刷新信息源调度注册表",
    )
    scheduler.add_job(
        _register_weekly_research_job,
        "interval",
        minutes=1,
        args=[scheduler],
        id="research-registry-refresh",
        name="刷新研究周报运行时开关",
    )
    scheduler.add_job(
        recover_capacity_blocked_research_batches_job,
        "interval",
        minutes=1,
        id="research-capacity-recovery",
        name="恢复容量阻塞研究批次",
    )
    scheduler.add_job(
        risk_validity_job,
        _trigger(SCHEDULER_EXPIRE_CRON),
        id="expire",
        name="风险有效期物化",
    )
    _notification_settings = get_notification_settings()
    if _notification_settings.enabled:
        scheduler.add_job(
            notify_job,
            "interval",
            seconds=_notification_settings.scan_interval_seconds,
            id="notify",
            name="风险提醒推送",
        )
        scheduler.add_job(
            monthly_report_job,
            _trigger(NOTIFY_MONTHLY_REPORT_CRON),
            id="notify-monthly",
            name="风险月报推送",
        )
    scheduler.add_job(
        cleanup_job, _trigger(SCHEDULER_CLEANUP_CRON), id="cleanup", name="保留清理"
    )
    scheduler.add_job(
        intelligence_report_job,
        _trigger(SCHEDULER_INTELLIGENCE_CRON),
        id="intelligence",
        name="政策与行业情报报告生成",
    )
    if RESEARCH_TRACK_ENABLED and RESEARCH_SCHEDULE_OWNER_USERNAME and RESEARCH_DAILY_TOPIC:
        scheduler.add_job(
            create_research_task_job,
            _trigger(RESEARCH_DAILY_CRON),
            args=["daily"],
            id="research-daily",
            name="创建每日研究任务",
        )
    if (
        RESEARCH_TRACK_ENABLED
        and RESEARCH_MONTHLY_ENABLED
        and RESEARCH_SCHEDULE_OWNER_USERNAME
        and RESEARCH_MONTHLY_TOPIC
    ):
        scheduler.add_job(
            create_monthly_research_batch_job,
            _trigger(RESEARCH_MONTHLY_CRON),
            id="research-monthly",
            name="创建月报批次",
        )
    logger.info(
        "Scheduler 启动: collect=%s expire=%s cleanup=%s intelligence=%s research_daily=%s "
        "research_weekly=%s research_monthly=%s notify=%s notify_monthly=%s signal_consume=%ss",
        SCHEDULER_COLLECT_CRON,
        SCHEDULER_EXPIRE_CRON,
        SCHEDULER_CLEANUP_CRON,
        SCHEDULER_INTELLIGENCE_CRON,
        (
            RESEARCH_DAILY_CRON
            if RESEARCH_TRACK_ENABLED and RESEARCH_SCHEDULE_OWNER_USERNAME and RESEARCH_DAILY_TOPIC
            else "disabled"
        ),
        "dynamic",
        (
            RESEARCH_MONTHLY_CRON
            if (
                RESEARCH_TRACK_ENABLED
                and RESEARCH_MONTHLY_ENABLED
                and RESEARCH_SCHEDULE_OWNER_USERNAME
                and RESEARCH_MONTHLY_TOPIC
            )
            else "disabled"
        ),
        (
            f"{_notification_settings.scan_interval_seconds}s"
            if _notification_settings.enabled
            else "disabled"
        ),
        (
            NOTIFY_MONTHLY_REPORT_CRON
            if _notification_settings.enabled
            else "disabled"
        ),
        SIGNAL_CONSUME_INTERVAL_SECONDS,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
