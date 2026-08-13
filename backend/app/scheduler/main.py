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
    RESEARCH_SCHEDULE_OWNER_USERNAME,
    RESEARCH_TRACK_ENABLED,
    RESEARCH_WEEKLY_CRON,
    RESEARCH_WEEKLY_TOPIC,
    SCHEDULER_CLEANUP_CRON,
    SCHEDULER_COLLECT_CRON,
    SCHEDULER_EXPIRE_CRON,
)
from app.database import SessionLocal
from app.scheduler.jobs import (
    _cron_to_apscheduler,
    cleanup_job,
    collect_job,
    collect_source_job,
    create_research_task_job,
    expire_job,
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


def main() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        collect_job, _trigger(SCHEDULER_COLLECT_CRON), id="collect", name="定时采集与处理"
    )
    _register_source_jobs(scheduler)
    scheduler.add_job(
        _register_source_jobs,
        "interval",
        minutes=1,
        args=[scheduler],
        id="registry-refresh",
        name="刷新数据源调度注册表",
    )
    scheduler.add_job(
        expire_job, _trigger(SCHEDULER_EXPIRE_CRON), id="expire", name="提醒失效"
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
    if RESEARCH_TRACK_ENABLED and RESEARCH_SCHEDULE_OWNER_USERNAME and RESEARCH_WEEKLY_TOPIC:
        scheduler.add_job(
            create_research_task_job,
            _trigger(RESEARCH_WEEKLY_CRON),
            args=["weekly"],
            id="research-weekly",
            name="创建每周研究任务",
        )
    logger.info(
        "Scheduler 启动: collect=%s expire=%s cleanup=%s research_daily=%s research_weekly=%s",
        SCHEDULER_COLLECT_CRON,
        SCHEDULER_EXPIRE_CRON,
        SCHEDULER_CLEANUP_CRON,
        (
            RESEARCH_DAILY_CRON
            if RESEARCH_TRACK_ENABLED and RESEARCH_SCHEDULE_OWNER_USERNAME and RESEARCH_DAILY_TOPIC
            else "disabled"
        ),
        (
            RESEARCH_WEEKLY_CRON
            if RESEARCH_TRACK_ENABLED and RESEARCH_SCHEDULE_OWNER_USERNAME and RESEARCH_WEEKLY_TOPIC
            else "disabled"
        ),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
