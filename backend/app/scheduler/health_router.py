"""只读监控健康聚合接口、Scheduler 原始日志窗口与按任务执行历史。

- ``GET /api/v1/system/monitoring-health``：沿用 ``source_status_view`` 权限，
  无会话 401、无权限 403（由 ``require_permission`` 统一处理）。
- ``GET /api/v1/system/scheduler-logs``：``system_log_view`` 权限，只读 tail
  共享卷中的 scheduler 日志；不接受路径参数，响应统一脱敏并禁止缓存。
- ``GET /api/v1/system/scheduler-runs/{job_id}``：同样沿用 ``source_status_view``
  权限，返回该任务最近若干次执行与脱敏结果摘要；job 已移除时返回空 items。
- 响应只含稳定枚举、计数、时间戳与脱敏后的日志文本/结果摘要，不暴露 endpoint、
  凭据、原始 retval、异常或日志目录。
- 原 ``/api/v1/system/health`` 完全不变，不在本模块内触碰。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.permissions import PERM_SOURCE_STATUS_VIEW, PERM_SYSTEM_LOG_VIEW
from app.auth.security import require_permission
from app.database import get_session
from app.scheduler.health import build_monitoring_health, recent_runs_for_job
from app.scheduler.health_schemas import (
    MonitoringHealthRead,
    SchedulerLogRead,
    SchedulerRunHistoryRead,
)
from app.scheduler.log_tail import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    read_log_tail,
    scheduler_log_path,
)

router = APIRouter(prefix="/api/v1/system", tags=["系统监控"])

SessionDependency = Annotated[Session, Depends(get_session)]
MonitoringViewer = Annotated[User, Depends(require_permission(PERM_SOURCE_STATUS_VIEW))]
LogViewer = Annotated[User, Depends(require_permission(PERM_SYSTEM_LOG_VIEW))]


@router.get("/monitoring-health", response_model=MonitoringHealthRead)
def get_monitoring_health(
    session: SessionDependency, viewer: MonitoringViewer
) -> MonitoringHealthRead:
    del viewer
    return build_monitoring_health(session)


@router.get("/scheduler-logs", response_model=SchedulerLogRead)
def get_scheduler_logs(
    response: Response,
    viewer: LogViewer,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> SchedulerLogRead:
    """读取 scheduler 日志末尾或游标之后的增量，仅授权管理员可见。"""
    del viewer
    page = read_log_tail(scheduler_log_path(), cursor, limit)
    response.headers["Cache-Control"] = "no-store"
    return SchedulerLogRead(
        available=page.available,
        reason=page.reason,
        lines=page.lines,
        next_cursor=page.next_cursor,
        reset=page.reset,
        truncated=page.truncated,
        updated_at=page.updated_at,
    )


@router.get("/scheduler-runs/{job_id}", response_model=SchedulerRunHistoryRead)
def get_scheduler_runs(
    response: Response,
    job_id: Annotated[
        str, Path(min_length=1, max_length=120, pattern=r"^[a-z0-9-]+$")
    ],
    session: SessionDependency,
    viewer: MonitoringViewer,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SchedulerRunHistoryRead:
    """按任务返回最近若干次执行；job 已移除时 items 为空，不返回 404。"""
    del viewer
    response.headers["Cache-Control"] = "no-store"
    return SchedulerRunHistoryRead(
        job_id=job_id,
        items=recent_runs_for_job(session, job_id=job_id, limit=limit),
    )
