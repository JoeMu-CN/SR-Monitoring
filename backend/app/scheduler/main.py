"""Scheduler 进程入口。

启动：python -m app.scheduler.main
依赖：PostgreSQL 已迁移（alembic upgrade head 由 compose 启动命令执行）。
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import (
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
    get_notification_settings,
)
from app.database import SessionLocal
from app.notification.service import notify_job
from app.research.schedule import get_schedule_config, weekly_schedule_preflight
from app.scheduler.jobs import (
    _cron_to_apscheduler,
    cleanup_job,
    collect_job,
    collect_source_job,
    collect_tyc_for_suppliers_job,
    create_monthly_research_batch_job,
    create_research_task_job,
    create_weekly_research_batch_job,
    expire_job,
    recover_capacity_blocked_research_batches_job,
)
from app.signals.models import DataSource

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("scheduler.main")


def _trigger(expr: str) -> CronTrigger:
    return CronTrigger(**_cron_to_apscheduler(expr), timezone="Asia/Shanghai")


def _register_source_jobs(scheduler: BlockingScheduler) -> None:
    """刷新数据源控制台中的独立 cron 任务。"""
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
                    name=f"采集数据源 {source.code}",
                )
            elif str(existing.trigger) != str(trigger):
                scheduler.reschedule_job(job_id, trigger=trigger)
        except ValueError as exc:
            logger.error("跳过非法数据源调度周期 %s=%s: %s", source.code, source.schedule, exc)


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


def main() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        collect_job, _trigger(SCHEDULER_COLLECT_CRON), id="collect", name="定时采集与处理"
    )
    # 供应商主体维度：每日批量天眼查核查（额度 10000/天，100000/月），结果落信号池
    scheduler.add_job(
        collect_tyc_for_suppliers_job,
        _trigger("0 6 * * *"),
        id="tyc-suppliers-daily",
        name="天眼查供应商批量核查",
    )
    _register_source_jobs(scheduler)
    _register_weekly_research_job(scheduler)
    scheduler.add_job(
        _register_source_jobs,
        "interval",
        minutes=1,
        args=[scheduler],
        id="registry-refresh",
        name="刷新数据源调度注册表",
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
        expire_job, _trigger(SCHEDULER_EXPIRE_CRON), id="expire", name="提醒失效"
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
        cleanup_job, _trigger(SCHEDULER_CLEANUP_CRON), id="cleanup", name="保留清理"
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
        "Scheduler 启动: collect=%s expire=%s cleanup=%s research_daily=%s "
        "research_weekly=%s research_monthly=%s notify=%s",
        SCHEDULER_COLLECT_CRON,
        SCHEDULER_EXPIRE_CRON,
        SCHEDULER_CLEANUP_CRON,
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
    )
    scheduler.start()


if __name__ == "__main__":
    main()
