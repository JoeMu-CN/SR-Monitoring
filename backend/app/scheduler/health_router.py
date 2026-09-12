"""只读监控健康聚合接口。

- 仅 ``GET /api/v1/system/monitoring-health``，沿用 ``source_status_view`` 权限。
- 无会话 401、无权限 403（由 ``require_permission`` 统一处理）。
- 响应只含稳定枚举、计数与时间戳，不暴露 endpoint、凭据、异常文本或堆栈。
- 原 ``/api/v1/system/health`` 完全不变，不在本模块内触碰。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.permissions import PERM_SOURCE_STATUS_VIEW
from app.auth.security import require_permission
from app.database import get_session
from app.scheduler.health import build_monitoring_health
from app.scheduler.health_schemas import MonitoringHealthRead

router = APIRouter(prefix="/api/v1/system", tags=["系统监控"])

SessionDependency = Annotated[Session, Depends(get_session)]
MonitoringViewer = Annotated[User, Depends(require_permission(PERM_SOURCE_STATUS_VIEW))]


@router.get("/monitoring-health", response_model=MonitoringHealthRead)
def get_monitoring_health(
    session: SessionDependency, viewer: MonitoringViewer
) -> MonitoringHealthRead:
    del viewer
    return build_monitoring_health(session)
