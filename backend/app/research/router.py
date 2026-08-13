"""研究任务 API；只负责短请求和任务治理，不执行长研究流程。"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import SecurityAuditEvent, User
from app.auth.security import (
    PERM_BUSINESS_AUDIT_VIEW,
    PERM_RESEARCH_TASK_CREATE,
    require_permission,
    verify_csrf,
    write_audit,
)
from app.database import get_session
from app.research.models import ResearchReport
from app.research.reporting import ReportValidationError, ResearchReportDraft
from app.research.schemas import (
    ReportReviewStatus,
    ReportStatus,
    ResearchAuditEventList,
    ResearchAuditEventRead,
    ResearchReportCreate,
    ResearchReportList,
    ResearchReportRead,
    ResearchTaskCreate,
    ResearchTaskList,
    ResearchTaskRead,
)
from app.research.service import (
    cancel_task,
    create_report,
    create_task,
    get_task,
    list_reports,
    list_tasks,
)

router = APIRouter(prefix="/api/v1/research", tags=["智能研究"])
SessionDependency = Annotated[Session, Depends(get_session)]
ResearchUser = Annotated[User, Depends(require_permission(PERM_RESEARCH_TASK_CREATE))]
ResearchAuditUser = Annotated[
    User, Depends(require_permission(PERM_BUSINESS_AUDIT_VIEW))
]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


@router.post("/tasks", response_model=ResearchTaskRead, status_code=status.HTTP_202_ACCEPTED)
def create_research_task(
    payload: ResearchTaskCreate,
    session: SessionDependency,
    user: ResearchUser,
    _csrf: CsrfGuard,
) -> ResearchTaskRead:
    if payload.task_type != "manual":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="daily/weekly 任务只能由 Scheduler 创建",
        )
    task = create_task(
        session,
        owner_user_id=user.id,
        task_type=payload.task_type,
        topic=payload.topic,
        supplier_scope=payload.supplier_scope,
        idempotency_key=payload.idempotency_key,
    )
    write_audit(
        session,
        action="research_task_created",
        actor_user_id=user.id,
        resource_type="research_task",
        resource_id=str(task.id),
        detail=f"task_type={task.task_type}",
    )
    session.commit()
    return ResearchTaskRead.model_validate(task)


@router.get("/tasks", response_model=ResearchTaskList)
def list_research_tasks(
    session: SessionDependency, user: ResearchUser
) -> ResearchTaskList:
    return ResearchTaskList(
        items=[
            ResearchTaskRead.model_validate(task)
            for task in list_tasks(session, owner_user_id=user.id, role=user.role)
        ]
    )


@router.get("/audit-logs", response_model=ResearchAuditEventList)
def list_research_audit_logs(
    session: SessionDependency,
    _user: ResearchAuditUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResearchAuditEventList:
    """仅返回研究生命周期事件，且只暴露业务审计所需的最小字段。"""
    actions = (
        "research_task_created",
        "research_task_claimed",
        "research_task_cancel_requested",
        "research_task_cancelled",
        "research_task_succeeded",
        "research_task_failed",
        "research_report_draft_created",
        "research_report_reviewed",
        "research_claim_promoted",
    )
    filters = [SecurityAuditEvent.action.in_(actions)]
    total = session.scalar(
        select(func.count()).select_from(SecurityAuditEvent).where(*filters)
    ) or 0
    events = list(
        session.scalars(
            select(SecurityAuditEvent)
            .where(*filters)
            .order_by(
                SecurityAuditEvent.occurred_at.desc(), SecurityAuditEvent.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return ResearchAuditEventList(
        items=[ResearchAuditEventRead.model_validate(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/tasks/{task_id}", response_model=ResearchTaskRead)
def get_research_task(
    task_id: int, session: SessionDependency, user: ResearchUser
) -> ResearchTaskRead:
    task = get_task(session, task_id=task_id, owner_user_id=user.id, role=user.role)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    return ResearchTaskRead.model_validate(task)


@router.post("/tasks/{task_id}/cancel", response_model=ResearchTaskRead)
def cancel_research_task(
    task_id: int,
    session: SessionDependency,
    user: ResearchUser,
    _csrf: CsrfGuard,
) -> ResearchTaskRead:
    task = cancel_task(session, task_id=task_id, owner_user_id=user.id, role=user.role)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    write_audit(
        session,
        action=(
            "research_task_cancelled"
            if task.status == "cancelled"
            else "research_task_cancel_requested"
        ),
        actor_user_id=user.id,
        resource_type="research_task",
        resource_id=str(task.id),
        detail=f"status={task.status}",
    )
    session.commit()
    return ResearchTaskRead.model_validate(task)


def _report_read(report: ResearchReport) -> ResearchReportRead:
    return ResearchReportRead(
        id=report.id,
        task_id=report.task_id,
        title=report.title,
        draft=ResearchReportDraft.model_validate(report.draft_payload),
        status=cast(ReportStatus, report.status),
        review_status=cast(ReportReviewStatus, report.review_status),
        model_version=report.model_version,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@router.post(
    "/tasks/{task_id}/reports",
    response_model=ResearchReportRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_research_report(
    task_id: int,
    payload: ResearchReportCreate,
    session: SessionDependency,
    user: ResearchUser,
    _csrf: CsrfGuard,
) -> ResearchReportRead:
    try:
        report = create_report(
            session,
            task_id=task_id,
            owner_user_id=user.id,
            role=user.role,
            draft=payload.draft,
            model_version=payload.model_version,
        )
    except ReportValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    write_audit(
        session,
        action="research_report_draft_created",
        actor_user_id=user.id,
        resource_type="research_report",
        resource_id=str(report.id),
        detail="status=draft review_status=pending",
    )
    session.commit()
    return _report_read(report)


@router.get("/tasks/{task_id}/reports", response_model=ResearchReportList)
def list_research_reports(
    task_id: int,
    session: SessionDependency,
    user: ResearchUser,
) -> ResearchReportList:
    reports = list_reports(
        session,
        task_id=task_id,
        owner_user_id=user.id,
        role=user.role,
    )
    if reports is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    return ResearchReportList(items=[_report_read(report) for report in reports])
