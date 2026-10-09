"""Scheduler 运行时观测写入：一律独立短事务、显式提交、失败不影响业务。

调用约定：
- 业务事务得出成功或失败结论 **之后**，再调用本模块写观测。
- 每次写入打开全新 ``SessionLocal`` 会话，只写 ``scheduler_runtime_state``，
  显式 ``commit``；任何异常只记稳定日志，绝不向上抛、绝不改写业务结果、
  也绝不把失败伪装成健康。
- 进程锁未获得的重复批次直接跳过，不在此写 ``failed``。
- 任务开始与结束都写状态：``record_job_started`` 置 running 并登记当前工作项，
  ``record_job_result`` 置 succeeded/failed；成功时间仅在成功结论后更新。
- 当前工作字段（current_kind/current_stage/current_item_id）只在任务运行期间
  有值：结束时由 ``record_job_result`` / ``record_job_neutral`` 一并清空，
  避免健康聚合把已结束任务继续当作“正在工作”。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.scheduler.runtime_models import (
    CurrentWorkKind,
    CurrentWorkStage,
    SchedulerRuntimeState,
)

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


def record_source_collection_deferred(
    source_id: int, *, now: datetime | None = None
) -> bool:
    """记录单个信源本次采集受控延后（域名冷却/租约/节流）。

    延后既非成功也非失败：中性完成，不刷新成功锚点，也不写入失败。
    """
    return record_job_neutral(source_collection_job_key(source_id), now=now)


def record_job_neutral(job_key: str, *, now: datetime | None = None) -> bool:
    """中性完成：状态收敛为 idle、清空 error_code 与当前工作项，保留 last_success_at。"""
    moment = now or datetime.now(UTC)
    return _write(
        job_key,
        {
            "status": "idle",
            "last_finished_at": moment,
            "error_code": None,
            "current_kind": None,
            "current_stage": None,
            "current_item_id": None,
            "updated_at": moment,
        },
    )


def record_job_started(
    job_key: str,
    *,
    kind: CurrentWorkKind,
    stage: CurrentWorkStage,
    item_id: int | None = None,
    now: datetime | None = None,
) -> bool:
    """任务开始时标记观测点为 running 并登记当前工作项（独立短事务；写失败不影响业务）。

    ``item_id`` 缺省即写入 NULL：新一轮开始必须覆盖上一轮残留的当前项，
    不允许继承旧观测。单信号粒度更新请用 ``record_pending_signal_started``。
    """
    moment = now or datetime.now(UTC)
    return _write(
        job_key,
        {
            "status": "running",
            "last_started_at": moment,
            "current_kind": kind,
            "current_stage": stage,
            "current_item_id": item_id,
            "updated_at": moment,
        },
    )


def record_pending_signal_started(signal_id: int, *, now: datetime | None = None) -> bool:
    """处理单个待处理信号前刷新当前工作项（item_id 与 stage）。

    不刷新批次级 ``last_started_at``：``pending_signals`` 行的开始时间仍表示
    本批次处理开始，而不是最后一个信号的开始。
    """
    moment = now or datetime.now(UTC)
    return _write(
        PENDING_SIGNALS_JOB_KEY,
        {
            "status": "running",
            "current_kind": "pending_signal_processing",
            "current_stage": "processing_signal",
            "current_item_id": signal_id,
            "updated_at": moment,
        },
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
        "current_kind": None,
        "current_stage": None,
        "current_item_id": None,
        "updated_at": moment,
    }
    if succeeded:
        values["last_success_at"] = moment
    return _write(job_key, values)
