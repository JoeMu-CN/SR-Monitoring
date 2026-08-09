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
    """将数据源控制台中的独立 cron 注册为 APScheduler 任务。"""
    with SessionLocal() as session:
        sources = list(
            session.scalars(
                select(DataSource)
                .where(DataSource.enabled.is_(True), DataSource.schedule.is_not(None))
                .order_by(DataSource.id)
            )
        )
    for source in sources:
        assert source.schedule is not None
        try:
            scheduler.add_job(
                collect_source_job,
                _trigger(source.schedule),
                args=[source.id],
                id=f"source-{source.id}",
                name=f"采集数据源 {source.code}",
                replace_existing=True,
            )
        except ValueError as exc:
            logger.error("跳过非法数据源调度周期 %s=%s: %s", source.code, source.schedule, exc)


def main() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        collect_job, _trigger(SCHEDULER_COLLECT_CRON), id="collect", name="定时采集与处理"
    )
    _register_source_jobs(scheduler)
    scheduler.add_job(
        expire_job, _trigger(SCHEDULER_EXPIRE_CRON), id="expire", name="提醒失效"
    )
    scheduler.add_job(
        cleanup_job, _trigger(SCHEDULER_CLEANUP_CRON), id="cleanup", name="保留清理"
    )
    logger.info(
        "Scheduler 启动: collect=%s expire=%s cleanup=%s",
        SCHEDULER_COLLECT_CRON,
        SCHEDULER_EXPIRE_CRON,
        SCHEDULER_CLEANUP_CRON,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
