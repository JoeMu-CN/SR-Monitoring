"""Scheduler 排期快照与执行历史的观测写入：独立短事务、失败不影响业务。

调用约定：
- 每次事件写入打开全新 ``SessionLocal`` 会话，只写本模块两张表，显式
  ``commit``；任何异常只记稳定日志，绝不向上抛、不改写业务结果。
- ``record_job_submitted`` 先插入 running 行；``record_job_outcome`` 可先到并
  UPSERT 终态；迟到的 SUBMITTED 绝不覆盖终态（``ON CONFLICT DO NOTHING``）。
- 时间一律规范化为 UTC；事件幂等键 ``(job_id, scheduled_run_at)`` 由数据库
  唯一约束兜底并发，事件乱序与 listener 并发下仍保持单行。
- registry 排除纯内部维护 job；history 额外排除高频 ``notify``，避免提醒扫描
  淹没历史；动态 ``source-*`` 必须记录。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.scheduler.observability_models import (
    JobRunStatus,
    SchedulerJobRegistry,
    SchedulerJobRun,
)

logger = logging.getLogger("scheduler.observability")

# registry 快照排除纯内部维护 job（不构成业务排期，展示无意义）。
REGISTRY_EXCLUDED_JOB_IDS: frozenset[str] = frozenset(
    {
        "runtime-heartbeat",
        "registry-refresh",
        "research-registry-refresh",
        "research-capacity-recovery",
    }
)
# 执行历史在内部维护 job 之外再排除高频 notify，避免历史被提醒扫描淹没。
HISTORY_EXCLUDED_JOB_IDS: frozenset[str] = REGISTRY_EXCLUDED_JOB_IDS | {"notify"}

# 终态中真正运行过的状态：记录 finished_at；missed/max_instances 从未运行。
_FINISHED_RUN_STATUSES: frozenset[str] = frozenset({"completed", "error"})


class JobLike(Protocol):
    """registry 快照所需的最小 job 视图（APScheduler ``Job`` 结构兼容）。

    只读属性协议：可写实例属性（真实 ``Job``）与只读 property（测试替身）都满足。
    """

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def next_run_time(self) -> datetime | None: ...


class JobSource(Protocol):
    """可列举全部 job 的调度器视图。"""

    def get_jobs(self) -> Sequence[JobLike]: ...


def _utc(value: datetime) -> datetime:
    """把时间戳规范化为 UTC；naive 值视为 UTC（与健康聚合口径一致）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _job_next_run_at(job: JobLike) -> datetime | None:
    """读取 job 的下一次触发时间；pending Job 可能按官方契约缺失该属性。

    APScheduler 3.11.3 在 ``scheduler.start()`` 的 pending 处理循环中逐个
    ``_real_add_job`` 并派发 JOB_ADDED；此时 ``scheduler.get_jobs()`` 仍走
    STOPPED 分支返回整批 ``_pending_jobs``，其中尚未处理的 Job 对象没有
    ``next_run_time`` 槽位，访问即抛 AttributeError。本函数只对该已知
    AttributeError 返回 None（如实表示暂无可用的真实排期，待后续 JOB_ADDED /
    SCHEDULER_STARTED 同步再落真实值），不吞并其它异常；属性存在时规范化为 UTC。
    """
    try:
        value = job.next_run_time
    except AttributeError:
        return None
    return _utc(value) if value is not None else None


def registered_job_name(job_id: str) -> str | None:
    """从 registry 回退解析 job 名称；读取失败返回 None，绝不向上抛。"""
    try:
        with SessionLocal() as session:
            return session.scalar(
                select(SchedulerJobRegistry.name).where(
                    SchedulerJobRegistry.job_id == job_id
                )
            )
    except Exception:
        logger.warning("scheduler_job_registry_read_failed job_id=%s", job_id)
        return None


def reconcile_interrupted_runs(*, now: datetime | None = None) -> bool:
    """收敛上次进程中断遗留的 running 行为稳定异常状态 ``error``。

    APScheduler 的 JOB_SUBMITTED 事件不保证一定有终态：Scheduler 进程在任务
    运行期间崩溃时，已写入的 ``running`` 行会永久停留，在健康聚合里被误报为
    “执行中”。Scheduler 每次启动时（``EVENT_SCHEDULER_STARTED`` 之后、同步
    registry 之前）调用本函数，把所有遗留 ``running`` 行收敛为上次进程中断后
    的稳定异常 ``error``，``finished_at`` 记为本次启动观测时间；``started_at``
    保留原始登记时间。只写稳定状态，不保存任何异常文本。

    幂等：只匹配 ``running`` 行，``completed``/``error``/``missed``/
    ``max_instances`` 一律不变；无遗留 running 时是空操作，重复调用无副作用。
    失败隔离：只记稳定日志并返回 False，绝不上抛、不影响业务 job。
    """
    moment = now or datetime.now(UTC)
    try:
        with SessionLocal() as session:
            session.execute(
                update(SchedulerJobRun)
                .where(SchedulerJobRun.status == "running")
                .values(status="error", finished_at=moment)
            )
            session.commit()
        return True
    except Exception:
        logger.warning("scheduler_job_run_reconcile_failed")
        return False


def sync_job_registry(scheduler: JobSource, *, now: datetime | None = None) -> bool:
    """把 Scheduler 的真实排期全量快照到 registry。

    - 每行 next_run_at 取自 ``Job.next_run_time``；无下一次触发时写 NULL。
    - 同一事务内 upsert 当前 job 并删除已不在调度器中的 job（移除即删除）。
    - 失败只记稳定日志并返回 False，不影响调度器。
    """
    moment = now or datetime.now(UTC)
    try:
        jobs = [
            job
            for job in scheduler.get_jobs()
            if job.id not in REGISTRY_EXCLUDED_JOB_IDS
        ]
        current_ids = {job.id for job in jobs}
        with SessionLocal() as session:
            for job in jobs:
                statement = insert(SchedulerJobRegistry).values(
                    job_id=job.id,
                    name=job.name or job.id,
                    next_run_at=_job_next_run_at(job),
                    updated_at=moment,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[SchedulerJobRegistry.job_id],
                    set_={
                        "name": statement.excluded.name,
                        "next_run_at": statement.excluded.next_run_at,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
                session.execute(statement)
            if current_ids:
                session.execute(
                    delete(SchedulerJobRegistry).where(
                        SchedulerJobRegistry.job_id.not_in(current_ids)
                    )
                )
            else:
                session.execute(delete(SchedulerJobRegistry))
            session.commit()
        return True
    except Exception:
        logger.warning("scheduler_job_registry_sync_failed")
        return False


def record_job_submitted(
    job_id: str,
    scheduled_run_times: Iterable[datetime],
    *,
    name: str,
    now: datetime | None = None,
) -> bool:
    """SUBMITTED 事件：为每个 ``scheduled_run_times`` 插入 running 行（复数语义）。

    时间规范化为 UTC；冲突（终态或既有 running）一律不覆盖：重复 SUBMITTED
    幂等，迟到的 SUBMITTED 不得把终态改回 running。
    """
    if job_id in HISTORY_EXCLUDED_JOB_IDS:
        return False
    times = [_utc(value) for value in scheduled_run_times]
    if not times:
        return False
    moment = now or datetime.now(UTC)
    try:
        with SessionLocal() as session:
            for run_at in times:
                statement = insert(SchedulerJobRun).values(
                    job_id=job_id,
                    name=name,
                    status="running",
                    scheduled_run_at=run_at,
                    started_at=moment,
                    finished_at=None,
                )
                statement = statement.on_conflict_do_nothing(
                    index_elements=[
                        SchedulerJobRun.job_id,
                        SchedulerJobRun.scheduled_run_at,
                    ]
                )
                session.execute(statement)
            session.commit()
        return True
    except Exception:
        logger.warning("scheduler_job_run_write_failed job_id=%s", job_id)
        return False


def record_job_outcome(
    job_id: str,
    scheduled_run_at: datetime,
    status: JobRunStatus,
    *,
    name: str,
    now: datetime | None = None,
) -> bool:
    """终态事件（EXECUTED/ERROR/MISSED/MAX_INSTANCES）：UPSERT 稳定终态。

    - 可先于 SUBMITTED 到达：直接插入终态行，``started_at`` 保持 NULL，不编造。
    - ``completed``/``error`` 记录 ``finished_at``；``missed``/``max_instances``
      从未运行，``started_at``/``finished_at`` 均保持 NULL。
    - 终态重复到达幂等：同一幂等键 UPSERT 到同一终态。
    """
    if job_id in HISTORY_EXCLUDED_JOB_IDS:
        return False
    moment = now or datetime.now(UTC)
    finished_at = moment if status in _FINISHED_RUN_STATUSES else None
    try:
        with SessionLocal() as session:
            statement = insert(SchedulerJobRun).values(
                job_id=job_id,
                name=name,
                status=status,
                scheduled_run_at=_utc(scheduled_run_at),
                started_at=None,
                finished_at=finished_at,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[
                    SchedulerJobRun.job_id,
                    SchedulerJobRun.scheduled_run_at,
                ],
                set_={
                    "name": statement.excluded.name,
                    "status": statement.excluded.status,
                    "finished_at": statement.excluded.finished_at,
                },
            )
            session.execute(statement)
            session.commit()
        return True
    except Exception:
        logger.warning("scheduler_job_run_write_failed job_id=%s", job_id)
        return False
