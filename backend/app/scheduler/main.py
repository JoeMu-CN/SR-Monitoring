"""Scheduler 进程入口。

启动：python -m app.scheduler.main
依赖：PostgreSQL 已迁移（alembic upgrade head 由 compose 启动命令执行）。
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import (
    SCHEDULER_CLEANUP_CRON,
    SCHEDULER_COLLECT_CRON,
    SCHEDULER_EXPIRE_CRON,
)
from app.scheduler.jobs import _cron_to_apscheduler, cleanup_job, collect_job, expire_job

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("scheduler.main")


def _trigger(expr: str) -> CronTrigger:
    return CronTrigger(**_cron_to_apscheduler(expr), timezone="Asia/Shanghai")


def main() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        collect_job, _trigger(SCHEDULER_COLLECT_CRON), id="collect", name="定时采集与处理"
    )
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
