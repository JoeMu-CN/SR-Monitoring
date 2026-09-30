"""天眼查批次互斥锁（计划 D7）：session 级 advisory lock + 独立专用连接。

- ``BATCH_ADVISORY_LOCK_KEY`` 必须与额度锁 ``QUOTA_ADVISORY_LOCK_KEY`` 不同：
  批次锁保护整批运行，额度锁保护每次工具的「检查-调用-记账」，语义不同不得合并；
- 使用 ``engine.connect()`` 的专用连接执行 ``pg_try_advisory_lock``，整批持有；
- 退出（含异常路径）在同一连接 ``pg_advisory_unlock`` 后关闭；
- 未获锁的调用方必须在任何 MCP/网关调用前退出，attempted_count 保持 0。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("scheduler")

# "TYCB"（int32 范围内）；与 tyc_quota.QUOTA_ADVISORY_LOCK_KEY 必须不同。
BATCH_ADVISORY_LOCK_KEY = 0x54594342


@contextmanager
def batch_advisory_lock(bind: Engine) -> Iterator[bool]:
    """尝试获取批次 session 级锁；yield 是否获得，退出时同连接释放并关闭。"""
    connection = bind.connect()
    acquired = False
    try:
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": BATCH_ADVISORY_LOCK_KEY},
            )
        )
        yield acquired
    finally:
        try:
            if acquired:
                connection.scalar(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": BATCH_ADVISORY_LOCK_KEY},
                )
        except SQLAlchemyError:
            # 解锁失败会让底层会话继续持锁：强制失效连接以结束会话（锁随会话释放）。
            logger.warning("天眼查批次锁解锁失败，强制失效连接释放")
            connection.invalidate()
        finally:
            connection.close()
