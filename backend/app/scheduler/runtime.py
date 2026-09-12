"""Scheduler 运行时观测写入：一律独立短事务、显式提交、失败不影响业务。

调用约定：
- 业务事务得出成功或失败结论 **之后**，再调用本模块写观测。
- 每次写入打开全新 ``SessionLocal`` 会话，只写 ``scheduler_runtime_state``，
  显式 ``commit``；任何异常只记稳定日志，绝不向上抛、绝不改写业务结果、
  也绝不把失败伪装成健康。
- 进程锁未获得的重复批次直接跳过，不在此写 ``failed``。
- 任务开始与结束都写状态：``record_job_started`` 置 running，
  ``record_job_result`` 置 succeeded/failed；成功时间仅在成功结论后更新。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.scheduler.runtime_models import SchedulerRuntimeState

logger = logging.getLogger("scheduler.runtime")

# 观测点固定 key（计划第 198 行）：scheduler 心跳、待处理信号任务、单源采集。
HEARTBEAT_JOB_KEY = "scheduler"
PENDING_SIGNALS_JOB_KEY = "pending_signals"


def source_collection_job_key(source_id: int) -> str:
    """单个信源采集观测行的固定 key：``collect:<source_id>``。"""
    return f"collect:{source_id}"


# 心跳节律与判定过期的阈值：职责明确的独立常量，不复用研究 Worker 配置。
HEARTBEAT_INTERVAL_SECONDS = 60
HEARTBEAT_STALE_SECONDS = 180

# 允许持久化的稳定脱敏错误码白名单（与模型/迁移正则约束一致）。
_STABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "collect_failed",
        "pending_processing_failed",
        "observation_unavailable",
        "unknown_error",
    }
)


def sanitize_error_code(raw: str | None) -> str | None:
    """把任意来源的错误标签收敛为稳定脱敏码；无法识别一律 ``unknown_error``。"""
    if raw is None:
        return None
    candidate = raw.strip().lower()
    if candidate in _STABLE_ERROR_CODES:
        return candidate
    return "unknown_error"


def _write(job_key: str, values: dict[str, object]) -> bool:
    """在独立短事务中 upsert 一行观测；失败只记稳定日志并返回 False。"""
    payload: dict[str, object] = {"job_key": job_key, **values}
    try:
        with SessionLocal() as session:
            statement = insert(SchedulerRuntimeState).values(**payload)
            statement = statement.on_conflict_do_update(
                index_elements=[SchedulerRuntimeState.job_key],
                set_={key: statement.excluded[key] for key in values},
            )
            session.execute(statement)
            session.commit()
        return True
    except Exception:
        # 观测写失败不改变业务结果，也不得伪报健康：只留稳定日志。
        logger.warning("scheduler_runtime_observation_write_failed job_key=%s", job_key)
        return False


def record_heartbeat(*, now: datetime | None = None) -> bool:
    """刷新 scheduler 进程心跳（``heartbeat_at``）；不改动上一轮业务结论字段。"""
    moment = now or datetime.now(UTC)
    return _write(
        HEARTBEAT_JOB_KEY,
        {"heartbeat_at": moment, "updated_at": moment},
    )


def record_source_collection(
    source_id: int, *, succeeded: bool, now: datetime | None = None
) -> bool:
    """记录单个信源最近一次采集结论；用作健康聚合的成功锚点（优先于被清理的 collection_runs）。"""
    return record_job_result(
        source_collection_job_key(source_id),
        succeeded=succeeded,
        error_code=None if succeeded else "collect_failed",
        now=now,
    )


def record_job_started(job_key: str, *, now: datetime | None = None) -> bool:
    """任务开始时标记观测点为 running（独立短事务；写失败不影响业务）。"""
    moment = now or datetime.now(UTC)
    return _write(
        job_key,
        {"status": "running", "last_started_at": moment, "updated_at": moment},
    )


def record_job_result(
    job_key: str,
    *,
    succeeded: bool,
    processed: int = 0,
    filtered: int = 0,
    failed: int = 0,
    error_code: str | None = None,
    now: datetime | None = None,
) -> bool:
    """在业务结论确定后写入本轮 succeeded/failed 结果与计数。

    ``last_success_at`` 仅在成功结论时更新；失败时保留上一次成功时间。
    """
    moment = now or datetime.now(UTC)
    stable_error = sanitize_error_code(error_code) if not succeeded else None
    values: dict[str, object] = {
        "status": "succeeded" if succeeded else "failed",
        "last_finished_at": moment,
        "processed_count": max(processed, 0),
        "filtered_count": max(filtered, 0),
        "failed_count": max(failed, 0),
        "error_code": stable_error,
        "updated_at": moment,
    }
    if succeeded:
        values["last_success_at"] = moment
    return _write(job_key, values)
