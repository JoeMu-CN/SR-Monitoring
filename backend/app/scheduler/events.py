"""APScheduler 事件 → 排期快照与执行历史的监听适配器。

- APScheduler 3.11.3 的事件对象没有 Job 实例：名称通过
  ``scheduler.get_job(job_id)`` 解析，缺失时回退 registry，再回退 job_id，
  禁止假定 ``event.job``。
- 精确事件语义：SUBMITTED/MAX_INSTANCES 使用复数 ``scheduled_run_times``；
  EXECUTED/ERROR/MISSED 使用单数 ``scheduled_run_time``。
- 生命周期事件（SCHEDULER_STARTED/JOB_ADDED/MODIFIED/REMOVED）与 SUBMITTED
  之后同步真实排期快照；移除的 job 从 registry 删除。
- 事件可能乱序且 listener 可并发：写入幂等由数据库唯一约束与 UPSERT 保证；
  监听器异常在最外层隔离并只记稳定日志，绝不影响业务 job。
- 监听器必须在 ``scheduler.start()`` 前注册（main.py 构造后立即注册）。
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Protocol

from apscheduler.events import (
    EVENT_JOB_ADDED,
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_MODIFIED,
    EVENT_JOB_REMOVED,
    EVENT_JOB_SUBMITTED,
    EVENT_SCHEDULER_STARTED,
    JobExecutionEvent,
    JobSubmissionEvent,
    SchedulerEvent,
)

import app.scheduler.observability as observability
from app.scheduler.observability import JobLike, JobSource
from app.scheduler.observability_models import JobRunStatus

logger = logging.getLogger("scheduler.events")


class _SchedulerView(JobSource, Protocol):
    """监听处理所需的调度器视图：注册监听 + 查询单个/全部 job。

    结构兼容 ``BlockingScheduler``，也可由测试注入最小替身（无需真实调度器）。
    """

    def add_listener(
        self, callback: Callable[[SchedulerEvent], None], mask: int
    ) -> None: ...

    def get_job(self, job_id: str) -> JobLike | None: ...


# 订阅集合：生命周期事件同步排期快照；执行事件写入历史。
OBSERVABILITY_EVENT_MASK = (
    EVENT_SCHEDULER_STARTED
    | EVENT_JOB_ADDED
    | EVENT_JOB_MODIFIED
    | EVENT_JOB_REMOVED
    | EVENT_JOB_SUBMITTED
    | EVENT_JOB_EXECUTED
    | EVENT_JOB_ERROR
    | EVENT_JOB_MISSED
    | EVENT_JOB_MAX_INSTANCES
)

_REGISTRY_SYNC_CODES = frozenset(
    {EVENT_SCHEDULER_STARTED, EVENT_JOB_ADDED, EVENT_JOB_MODIFIED, EVENT_JOB_REMOVED}
)
_TERMINAL_STATUS_BY_CODE: dict[int, JobRunStatus] = {
    EVENT_JOB_EXECUTED: "completed",
    EVENT_JOB_ERROR: "error",
    EVENT_JOB_MISSED: "missed",
}


def _resolve_job_name(scheduler: _SchedulerView, job_id: str) -> str:
    """名称解析：scheduler.get_job 优先，其次 registry，最后回退 job_id。"""
    job = scheduler.get_job(job_id)
    if job is not None and job.name:
        return str(job.name)
    registered = observability.registered_job_name(job_id)
    return registered or job_id


def _handle_submitted(scheduler: _SchedulerView, event: JobSubmissionEvent) -> None:
    job_id = event.job_id
    run_times = list(event.scheduled_run_times or ())
    observability.record_job_submitted(
        job_id, run_times, name=_resolve_job_name(scheduler, job_id)
    )
    # SUBMITTED 之后 next_run_time 已推进：同步真实排期快照。
    observability.sync_job_registry(scheduler)


def _handle_max_instances(
    scheduler: _SchedulerView, event: JobSubmissionEvent
) -> None:
    job_id = event.job_id
    run_times = list(event.scheduled_run_times or ())
    name = _resolve_job_name(scheduler, job_id)
    for run_time in run_times:
        observability.record_job_outcome(job_id, run_time, "max_instances", name=name)


def _handle_terminal(scheduler: _SchedulerView, event: JobExecutionEvent) -> None:
    status = _TERMINAL_STATUS_BY_CODE[event.code]
    run_time = event.scheduled_run_time
    if run_time is None:
        return
    job_id = event.job_id
    observability.record_job_outcome(
        job_id, run_time, status, name=_resolve_job_name(scheduler, job_id)
    )


def on_scheduler_event(scheduler: _SchedulerView, event: SchedulerEvent) -> None:
    """监听器入口：异常在最外层隔离，绝不向上抛、不影响业务 job。"""
    try:
        code = event.code
        if code == EVENT_SCHEDULER_STARTED:
            # 进程重启：先收敛上次中断遗留的 running 行，再同步真实排期快照。
            observability.reconcile_interrupted_runs()
            observability.sync_job_registry(scheduler)
        elif code in _REGISTRY_SYNC_CODES:
            observability.sync_job_registry(scheduler)
        elif code == EVENT_JOB_SUBMITTED and isinstance(event, JobSubmissionEvent):
            _handle_submitted(scheduler, event)
        elif code == EVENT_JOB_MAX_INSTANCES and isinstance(event, JobSubmissionEvent):
            _handle_max_instances(scheduler, event)
        elif code in _TERMINAL_STATUS_BY_CODE and isinstance(event, JobExecutionEvent):
            _handle_terminal(scheduler, event)
    except Exception:
        logger.warning(
            "scheduler_observability_event_failed code=%s", getattr(event, "code", None)
        )


def attach_observability_listener(scheduler: _SchedulerView) -> None:
    """在 ``scheduler.start()`` 前注册监听器；mask 覆盖快照与执行事件。"""
    listener: Callable[[SchedulerEvent], None] = functools.partial(
        on_scheduler_event, scheduler
    )
    scheduler.add_listener(listener, OBSERVABILITY_EVENT_MASK)
