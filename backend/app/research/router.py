"""研究任务 API；只负责短请求和任务治理，不执行长研究流程。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import SecurityAuditEvent, User
from app.auth.security import (
    PERM_BUSINESS_AUDIT_VIEW,
    PERM_RESEARCH_SCHEDULE_MANAGE,
    PERM_RESEARCH_TASK_CREATE,
    require_permission,
    verify_csrf,
    write_audit,
)
from app.config import RESEARCH_WORKER_HEARTBEAT_STALE_SECONDS
from app.database import get_session
from app.research.models import ResearchBatch, ResearchReport, ResearchTask
from app.research.reporting import ReportValidationError, ResearchReportDraft
from app.research.schedule import (
    DEFAULT_WEEKLY_BUDGET,
    ResearchScheduleConfigurationError,
    ResearchSchedulePreflightBlocked,
    get_schedule_config,
    save_weekly_schedule_config,
    weekly_schedule_preflight,
)
from app.research.schemas import (
    BatchPeriodType,
    BatchStatus,
    ReportReviewStatus,
    ReportStatus,
    ResearchAuditEventList,
    ResearchAuditEventRead,
    ResearchBatchList,
    ResearchBatchRead,
    ResearchBatchTaskRead,
    ResearchReportCreate,
    ResearchReportList,
    ResearchReportRead,
    ResearchSchedulePreflightRead,
    ResearchScheduleRead,
    ResearchScheduleUpdate,
    ResearchSourceList,
    ResearchSourceRead,
    ResearchTaskCreate,
    ResearchTaskEventList,
    ResearchTaskEventRead,
    ResearchTaskList,
    ResearchTaskRead,
    ResearchWorkerRead,
    ResearchWorkerStatusRead,
    TaskStatus,
    WorkerOverallStatus,
    WorkerRuntimeStatus,
)
from app.research.service import (
    ResearchTaskDeleteError,
    ResearchTaskStartError,
    append_task_event,
    cancel_batch,
    cancel_task,
    create_report,
    create_task,
    delete_task,
    get_batch,
    get_task,
    list_batches,
    list_reports,
    list_sources,
    list_task_events,
    list_tasks,
    list_worker_heartbeats,
    request_controlled_execution,
)

router = APIRouter(prefix="/api/v1/research", tags=["智能研究"])
SessionDependency = Annotated[Session, Depends(get_session)]
ResearchUser = Annotated[User, Depends(require_permission(PERM_RESEARCH_TASK_CREATE))]
ResearchAuditUser = Annotated[
    User, Depends(require_permission(PERM_BUSINESS_AUDIT_VIEW))
]
ResearchScheduleUser = Annotated[
    User, Depends(require_permission(PERM_RESEARCH_SCHEDULE_MANAGE))
]
CsrfGuard = Annotated[None, Depends(verify_csrf)]


def _weekly_schedule_read(session: Session) -> ResearchScheduleRead:
    config = get_schedule_config(session, schedule_type="weekly")
    cron_expression = config.cron_expression if config else "30 8 * * mon"
    topic_template = config.topic_template if config else ""
    budget_template = config.budget_template if config else dict(DEFAULT_WEEKLY_BUDGET)
    approved_monthly_quota = config.approved_monthly_quota if config else None
    preflight = weekly_schedule_preflight(
        session,
        cron_expression=cron_expression,
        topic_template=topic_template,
        budget_template=budget_template,
        approved_monthly_quota=approved_monthly_quota,
    )
    return ResearchScheduleRead(
        id=config.id if config else None,
        schedule_type="weekly",
        enabled=config.enabled if config else False,
        cron_expression=cron_expression,
        topic_template=topic_template,
        budget_template=budget_template,
        approved_monthly_quota=approved_monthly_quota,
        approved_by_user_id=config.approved_by_user_id if config else None,
        approved_at=config.approved_at if config else None,
        approval_note=config.approval_note if config else None,
        updated_by_user_id=config.updated_by_user_id if config else None,
        created_at=config.created_at if config else None,
        updated_at=config.updated_at if config else None,
        preflight=ResearchSchedulePreflightRead.model_validate(preflight.as_dict()),
    )


def _batch_read(session: Session, batch: ResearchBatch) -> ResearchBatchRead:
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.batch_id == batch.id)
            .order_by(ResearchTask.id)
        )
    )
    task_ids = [task.id for task in tasks]
    reports = (
        list(
            session.scalars(
                select(ResearchReport).where(ResearchReport.task_id.in_(task_ids))
            )
        )
        if task_ids
        else []
    )
    report_by_task = {report.task_id: report.id for report in reports if report.task_id is not None}
    supplier_info: dict[int, tuple[str | None, str | None]] = {}
    for item in batch.supplier_snapshot:
        raw_id = item.get("supplier_id")
        if isinstance(raw_id, int):
            supplier_info[raw_id] = (
                str(item["supplier_code"]) if item.get("supplier_code") is not None else None,
                str(item["legal_name"]) if item.get("legal_name") is not None else None,
            )
    task_items = []
    for task in tasks:
        supplier_id = task.supplier_scope[0] if len(task.supplier_scope) == 1 else None
        supplier_code, supplier_name = (
            supplier_info.get(supplier_id, (None, None))
            if supplier_id is not None
            else (None, None)
        )
        task_items.append(
            ResearchBatchTaskRead(
                id=task.id,
                supplier_id=supplier_id,
                supplier_code=supplier_code,
                supplier_name=supplier_name,
                status=cast(TaskStatus, task.status),
                current_step=task.current_step,
                error=task.error,
                report_id=report_by_task.get(task.id),
            )
        )
    aggregate_report = session.scalar(
        select(ResearchReport).where(ResearchReport.batch_id == batch.id)
    )
    return ResearchBatchRead(
        id=batch.id,
        owner_user_id=batch.owner_user_id,
        period_type=cast(BatchPeriodType, batch.period_type),
        period_key=batch.period_key,
        period_start=batch.period_start,
        period_end=batch.period_end,
        supplier_snapshot=batch.supplier_snapshot,
        supplier_count=batch.supplier_count,
        queued_count=batch.queued_count,
        running_count=batch.running_count,
        succeeded_count=batch.succeeded_count,
        failed_count=batch.failed_count,
        skipped_count=batch.skipped_count,
        budget_exhausted_count=batch.budget_exhausted_count,
        budget_snapshot=batch.budget_snapshot,
        status=cast(BatchStatus, batch.status),
        graph_version=batch.graph_version,
        created_at=batch.created_at,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        cancel_requested_at=batch.cancel_requested_at,
        error=batch.error,
        report_id=aggregate_report.id if aggregate_report else None,
        tasks=task_items,
    )


@router.get("/batches", response_model=ResearchBatchList)
def list_research_batches(
    session: SessionDependency, user: ResearchUser
) -> ResearchBatchList:
    return ResearchBatchList(
        items=[_batch_read(session, batch) for batch in list_batches(
            session, owner_user_id=user.id, role=user.role
        )]
    )


@router.get("/schedules/weekly", response_model=ResearchScheduleRead)
def get_weekly_research_schedule(
    session: SessionDependency, _user: ResearchUser
) -> ResearchScheduleRead:
    """读取周报关闭态、批准记录和后端额度/容量预检。"""
    return _weekly_schedule_read(session)


@router.put("/schedules/weekly", response_model=ResearchScheduleRead)
def update_weekly_research_schedule(
    payload: ResearchScheduleUpdate,
    session: SessionDependency,
    user: ResearchScheduleUser,
    _csrf: CsrfGuard,
) -> ResearchScheduleRead:
    try:
        save_weekly_schedule_config(
            session,
            updated_by_user_id=user.id,
            enabled=payload.enabled,
            cron_expression=payload.cron_expression,
            topic_template=payload.topic_template,
            budget_template=payload.budget_template,
            approved_monthly_quota=payload.approved_monthly_quota,
            approval_note=payload.approval_note,
        )
    except ResearchSchedulePreflightBlocked as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "preflight": exc.preflight.as_dict(),
            },
        ) from exc
    except ResearchScheduleConfigurationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    write_audit(
        session,
        action="research_schedule_updated",
        actor_user_id=user.id,
        resource_type="research_schedule_config",
        resource_id="weekly",
        detail=f"enabled={payload.enabled}",
    )
    session.commit()
    return _weekly_schedule_read(session)


@router.get("/batches/{batch_id}", response_model=ResearchBatchRead)
def get_research_batch(
    batch_id: int, session: SessionDependency, user: ResearchUser
) -> ResearchBatchRead:
    batch = get_batch(
        session, batch_id=batch_id, owner_user_id=user.id, role=user.role
    )
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究批次不存在")
    return _batch_read(session, batch)


@router.get("/batches/{batch_id}/reports", response_model=ResearchReportList)
def list_research_batch_reports(
    batch_id: int, session: SessionDependency, user: ResearchUser
) -> ResearchReportList:
    batch = get_batch(
        session, batch_id=batch_id, owner_user_id=user.id, role=user.role
    )
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究批次不存在")
    reports = list(
        session.scalars(
            select(ResearchReport)
            .where(ResearchReport.batch_id == batch.id)
            .order_by(ResearchReport.created_at.desc(), ResearchReport.id.desc())
        )
    )
    return ResearchReportList(items=[_report_read(report) for report in reports])


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
        source_urls=payload.source_urls,
        idempotency_key=payload.idempotency_key,
    )
    append_task_event(
        session,
        task_id=task.id,
        event_type="task_created",
        node_key="task",
        status="pending",
        label="研究任务已创建",
        detail={"task_type": task.task_type},
        once=True,
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


@router.get("/worker/status", response_model=ResearchWorkerStatusRead)
def get_research_worker_status(
    session: SessionDependency, _user: ResearchUser
) -> ResearchWorkerStatusRead:
    """返回 Worker 最近心跳，帮助区分未启动、过期和正常运行。"""
    checked_at = datetime.now(UTC)
    stale_after_seconds = RESEARCH_WORKER_HEARTBEAT_STALE_SECONDS
    stale_before = checked_at - timedelta(seconds=stale_after_seconds)
    workers: list[ResearchWorkerRead] = []
    for heartbeat in list_worker_heartbeats(session):
        last_seen_at = heartbeat.last_seen_at
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        runtime_status: WorkerRuntimeStatus
        if heartbeat.status == "stopped":
            runtime_status = "stopped"
        elif last_seen_at >= stale_before:
            runtime_status = "online"
        else:
            runtime_status = "stale"
        workers.append(
            ResearchWorkerRead(
                worker_id=heartbeat.worker_id,
                mode=heartbeat.mode,
                orchestrator=cast(Literal["legacy", "langgraph"], heartbeat.orchestrator),
                status=runtime_status,
                started_at=heartbeat.started_at,
                last_seen_at=heartbeat.last_seen_at,
                stopped_at=heartbeat.stopped_at,
            )
        )
    if any(worker.status == "online" for worker in workers):
        overall_status: WorkerOverallStatus = "online"
    elif any(worker.status == "stale" for worker in workers):
        overall_status = "stale"
    else:
        overall_status = "offline"
    return ResearchWorkerStatusRead(
        checked_at=checked_at,
        stale_after_seconds=stale_after_seconds,
        status=overall_status,
        workers=workers,
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
        "research_task_execution_requested",
        "research_task_claimed",
        "research_task_cancel_requested",
        "research_task_cancelled",
        "research_task_deleted",
        "research_task_succeeded",
        "research_task_failed",
        "research_report_draft_created",
        "research_report_reviewed",
        "research_claim_promoted",
        "research_schedule_updated",
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


@router.get("/tasks/{task_id}/events", response_model=ResearchTaskEventList)
def list_research_task_events(
    task_id: int,
    session: SessionDependency,
    user: ResearchUser,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> ResearchTaskEventList:
    events = list_task_events(
        session,
        task_id=task_id,
        owner_user_id=user.id,
        role=user.role,
        after_id=after_id,
        limit=limit,
    )
    if events is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    return ResearchTaskEventList(
        items=[ResearchTaskEventRead.model_validate(event) for event in events],
        next_after_id=events[-1].id if events else after_id,
    )


@router.post("/tasks/{task_id}/start", response_model=ResearchTaskRead)
def start_research_task(
    task_id: int,
    session: SessionDependency,
    user: ResearchUser,
    _csrf: CsrfGuard,
) -> ResearchTaskRead:
    try:
        task = request_controlled_execution(
            session, task_id=task_id, owner_user_id=user.id, role=user.role
        )
    except ResearchTaskStartError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    append_task_event(
        session,
        task_id=task.id,
        event_type="execution_requested",
        node_key="execution",
        parent_node_key="task",
        status="pending",
        label="等待研究 Worker",
        once=True,
    )
    write_audit(
        session,
        action="research_task_execution_requested",
        actor_user_id=user.id,
        resource_type="research_task",
        resource_id=str(task.id),
        detail="mode=topic_source_discovery",
    )
    session.commit()
    return ResearchTaskRead.model_validate(task)


@router.get("/tasks/{task_id}/sources", response_model=ResearchSourceList)
def list_research_sources(
    task_id: int,
    session: SessionDependency,
    user: ResearchUser,
) -> ResearchSourceList:
    sources = list_sources(
        session,
        task_id=task_id,
        owner_user_id=user.id,
        role=user.role,
    )
    if sources is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    return ResearchSourceList(
        items=[ResearchSourceRead.model_validate(source) for source in sources]
    )


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
    append_task_event(
        session,
        task_id=task.id,
        event_type=("task_cancelled" if task.status == "cancelled" else "task_cancel_requested"),
        node_key="task",
        status=("succeeded" if task.status == "cancelled" else "info"),
        label=("研究任务已取消" if task.status == "cancelled" else "已请求取消研究任务"),
        once=True,
    )
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


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_task(
    task_id: int,
    session: SessionDependency,
    user: ResearchUser,
    _csrf: CsrfGuard,
) -> None:
    try:
        task = delete_task(
            session,
            task_id=task_id,
            owner_user_id=user.id,
            role=user.role,
        )
    except ResearchTaskDeleteError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if task is None:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究任务不存在")
    write_audit(
        session,
        action="research_task_deleted",
        actor_user_id=user.id,
        resource_type="research_task",
        resource_id=str(task.id),
        detail=f"status={task.status};task_type={task.task_type}",
    )
    session.commit()


@router.post("/batches/{batch_id}/cancel", response_model=ResearchBatchRead)
def cancel_research_batch(
    batch_id: int,
    session: SessionDependency,
    user: ResearchUser,
    _csrf: CsrfGuard,
) -> ResearchBatchRead:
    batch = cancel_batch(
        session,
        batch_id=batch_id,
        owner_user_id=user.id,
        role=user.role,
    )
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究批次不存在")
    tasks = list(
        session.scalars(select(ResearchTask).where(ResearchTask.batch_id == batch.id))
    )
    for task in tasks:
        task_cancelled = task.status == "cancelled"
        append_task_event(
            session,
            task_id=task.id,
            event_type=("task_cancelled" if task_cancelled else "task_cancel_requested"),
            node_key="task",
            status="succeeded" if task_cancelled else "info",
            label=("研究子任务已取消" if task_cancelled else "已请求取消研究子任务"),
            detail={"batch_id": batch.id},
            once=True,
        )
    write_audit(
        session,
        action=(
            "research_batch_cancel_requested"
            if batch.status == "running"
            else "research_batch_cancelled"
        ),
        actor_user_id=user.id,
        resource_type="research_batch",
        resource_id=str(batch.id),
        detail=f"status={batch.status}",
    )
    session.commit()
    return _batch_read(session, batch)


def _report_read(report: ResearchReport) -> ResearchReportRead:
    return ResearchReportRead(
        id=report.id,
        task_id=report.task_id,
        batch_id=report.batch_id,
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
