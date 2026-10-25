"""只读监控健康聚合接口与 Scheduler 原始日志窗口。

- ``GET /api/v1/system/monitoring-health``：沿用 ``source_status_view`` 权限，
  无会话 401、无权限 403（由 ``require_permission`` 统一处理）。
- ``GET /api/v1/system/scheduler-logs``：``system_log_view`` 权限，只读 tail
  共享卷中的 scheduler 日志；不接受路径参数，响应统一脱敏并禁止缓存。
- 响应只含稳定枚举、计数、时间戳与脱敏后的日志文本，不暴露 endpoint、
  凭据或日志目录。
- 原 ``/api/v1/system/health`` 完全不变，不在本模块内触碰。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.permissions import PERM_SOURCE_STATUS_VIEW, PERM_SYSTEM_LOG_VIEW
from app.auth.security import require_permission
from app.database import get_session
from app.scheduler.health import build_monitoring_health
from app.scheduler.health_schemas import MonitoringHealthRead, SchedulerLogRead
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
