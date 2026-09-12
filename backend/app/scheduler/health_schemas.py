"""只读监控健康聚合的响应模型（计划第 200 行契约）。

顶层 ``as_of`` / ``overall`` / ``scheduler`` / ``processing`` / ``sources``；
每来源至少 ``source_id`` / ``last_success_at`` / ``last_attempt_at`` /
``next_expected_at`` / ``state`` / ``reason_code``。响应只暴露稳定枚举、
计数与时间戳；绝不包含 endpoint、凭据、异常文本或堆栈。
本轮尚未发布，不保留任何旧字段兼容层。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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


class SchedulerHealthRead(BaseModel):
    """scheduler 进程心跳观测（``scheduler`` 行的 ``heartbeat_at``）。"""

    status: HeartbeatStatus
    last_heartbeat_at: datetime | None
    age_seconds: int | None
    interval_seconds: int
    stale_after_seconds: int


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
