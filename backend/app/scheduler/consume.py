"""独立信号消费循环：与采集触发解耦，按固定间隔 + 单轮时间预算消费队列。

口径：
- 采集（``jobs.collect_*``）只入库，不再顺手消费；本模块由 Scheduler 以
  ``interval`` 方式触发，每轮在 ``SIGNAL_CONSUME_TIME_BUDGET_SECONDS`` 预算内
  反复调用 ``process_signal_batch``（每批 ``SIGNAL_CONSUME_BATCH_SIZE`` 条），
  队列为空（全零结果）即提前结束。
- 跨进程/跨副本互斥用 PostgreSQL **会话级** advisory lock（固定 key
  ``0x53474E4C``，即 "SGNL"）：加锁与解锁必须在**同一连接**上，因此锁由
  独立的 ``SessionLocal`` 持有，业务处理另开 session（``process_signal_batch``
  内部自开）。拿不到锁直接跳过本轮，不写失败观测。
- 观测与旧链路同口径：每批开始写 ``running``，结束写 succeeded/failed；
  批次异常只记日志、写失败观测后结束本轮，绝不向上抛（调度层边界）。
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import (
    SIGNAL_CONSUME_BATCH_SIZE,
    SIGNAL_CONSUME_ENABLED,
    SIGNAL_CONSUME_TIME_BUDGET_SECONDS,
)
from app.database import engine
from app.scheduler import runtime
from app.scheduler.jobs import process_signal_batch

logger = logging.getLogger("scheduler.consume")

# 固定 advisory lock key：0x53474E4C = "SGNL"。会话级锁，跨 Scheduler 副本互斥。
SIGNAL_CONSUME_LOCK_KEY = 0x53474E4C


def _try_acquire_consume_lock(connection: Connection) -> bool:
    """在给定连接上尝试取会话级 advisory lock（测试可 monkeypatch 隔离）。

    PostgreSQL 之外的环境（如 SQLite）没有该函数；调用方把异常收敛为本轮跳过，
    因此这里不做数据库方言分支。
    """
    return bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": SIGNAL_CONSUME_LOCK_KEY},
        ).scalar()
    )


def _release_consume_lock(connection: Connection) -> None:
    """释放同一连接持有的 advisory lock（必须与加锁同连接）。"""
    connection.execute(
        text("SELECT pg_advisory_unlock(:key)"),
        {"key": SIGNAL_CONSUME_LOCK_KEY},
    )


def consume_pending_signals_job() -> int:
    """消费一轮待分析队列，返回本轮累计 processed 条数。

    未启用直接返回 0；拿不到 advisory lock 说明另一轮仍在运行，本轮跳过；
    单轮受时间预算约束，队列空（全零）即停。

    锁必须由一个 ``engine.connect()`` 的**长期连接**持有：Session 在 commit 后
    会把连接归还连接池，可能导致解锁落在另一条连接上而让锁永久残留。
    """
    if not SIGNAL_CONSUME_ENABLED:
        return 0

    processed_total = 0
    filtered_total = 0
    failed_total = 0
    with engine.connect() as lock_connection:
        try:
            acquired = _try_acquire_consume_lock(lock_connection)
        except Exception:
            # 加锁本身不可用（例如非 PostgreSQL 后端）：本轮跳过，不伪报失败批次。
            logger.exception("信号消费锁不可用，跳过本轮")
            return 0
        if not acquired:
            logger.info("信号消费锁被占用（另一轮仍在运行），跳过本轮")
            return 0
        try:
            deadline = time.monotonic() + SIGNAL_CONSUME_TIME_BUDGET_SECONDS
            while True:
                runtime.record_job_started(
                    runtime.PENDING_SIGNALS_JOB_KEY,
                    kind="pending_signal_processing",
                    stage="processing_signal",
                )
                try:
                    result = process_signal_batch(SIGNAL_CONSUME_BATCH_SIZE)
                except Exception:
                    logger.exception("信号消费批次失败，结束本轮")
                    runtime.record_job_result(
                        runtime.PENDING_SIGNALS_JOB_KEY,
                        succeeded=False,
                        failed=1,
                        error_code="pending_processing_failed",
                    )
                    return processed_total
                processed_total += result.processed
                filtered_total += result.filtered
                failed_total += result.failed
                # 队列已空：没有任何可处理/可过滤/可失败的候选，提前收工。
                if result.processed == 0 and result.filtered == 0 and result.failed == 0:
                    break
                if time.monotonic() >= deadline:
                    break
        finally:
            try:
                _release_consume_lock(lock_connection)
            except Exception:
                # 解锁失败：连接关闭时 PostgreSQL 会自动释放会话级锁。
                logger.warning("信号消费锁释放失败，连接关闭后将自动释放")
    runtime.record_job_result(
        runtime.PENDING_SIGNALS_JOB_KEY,
        succeeded=True,
        processed=processed_total,
        filtered=filtered_total,
        failed=failed_total,
    )
    return processed_total
