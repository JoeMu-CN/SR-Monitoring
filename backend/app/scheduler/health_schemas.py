"""只读监控健康聚合的响应模型（计划第 200 行契约）。

顶层 ``as_of`` / ``overall`` / ``scheduler`` / ``processing`` / ``sources``；
每来源至少 ``source_id`` / ``last_success_at`` / ``last_attempt_at`` /
``next_expected_at`` / ``state`` / ``reason_code``。响应只暴露稳定枚举、
计数与时间戳；绝不包含 endpoint、凭据、异常文本或堆栈。
``scheduler.current_work`` 只投影运行中的任务项（kind / job_key / source_id /
source_name / stage / started_at / item_id），绝不包含信号标题或正文。
``scheduler.scheduled_jobs`` 是 Scheduler 进程写库的真实排期快照；
``scheduler.recent_runs`` 是最近调度执行历史（completed 只表示 callable
正常返回，绝不命名为 succeeded），均不含异常、堆栈或返回值。
本轮尚未发布，不保留任何旧字段兼容层。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.scheduler.observability_models import JobRunStatus
from app.scheduler.runtime_models import CurrentWorkKind, CurrentWorkStage

OverallStatus = Literal["ok", "degraded", "unknown", "inactive"]
HeartbeatStatus = Literal["unknown", "ok", "stale"]
SourceHealthStatus = Literal[
    "ok",
    "failed",
    "overdue",
    "never_run",
    "disabled",
    "on_demand",
    "invalid_schedule",
]
RunStatus = Literal["idle", "running", "succeeded", "failed"]


class CurrentWorkRead(BaseModel):
    """调度器当前正在运行的工作项；信源采集与待处理信号两个已知观测点。"""

    kind: CurrentWorkKind
    job_key: str
    source_id: int | None
    source_name: str | None
    stage: CurrentWorkStage
    started_at: datetime | None
    item_id: int | None


class ScheduledJobRead(BaseModel):
    """真实排期快照：next_run_at 来自 Scheduler 的 Job.next_run_time，Web 不推算。"""

    job_id: str
    name: str
    next_run_at: datetime | None


class SchedulerJobRunRead(BaseModel):
    """一次调度执行历史；completed 只表示 callable 正常返回，绝不命名为 succeeded。"""

    id: int
    job_id: str
    name: str
    status: JobRunStatus
    scheduled_run_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SchedulerHealthRead(BaseModel):
    """scheduler 进程心跳观测（``scheduler`` 行的 ``heartbeat_at``）。"""

    status: HeartbeatStatus
    last_heartbeat_at: datetime | None
    age_seconds: int | None
    interval_seconds: int
    stale_after_seconds: int
    current_work: list[CurrentWorkRead]
    scheduled_jobs: list[ScheduledJobRead]
    recent_runs: list[SchedulerJobRunRead]


class ProcessingRunRead(BaseModel):
    """``pending_signals`` 最近一轮处理的开始/结束与计数。"""

    status: RunStatus
    started_at: datetime | None
    finished_at: datetime | None
    processed: int
    filtered: int
    failed: int


class ProcessingHealthRead(BaseModel):
    total: int
    classification_failed: int
    backlog_over_1h: int
    oldest_pending_age_seconds: int | None
    last_run: ProcessingRunRead


class SourceHealthRead(BaseModel):
    source_id: int
    code: str
    name: str
    state: SourceHealthStatus
    reason_code: str
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    next_expected_at: datetime | None


class MonitoringHealthRead(BaseModel):
    as_of: datetime
    overall: OverallStatus
    scheduler: SchedulerHealthRead
    processing: ProcessingHealthRead
    sources: list[SourceHealthRead]
