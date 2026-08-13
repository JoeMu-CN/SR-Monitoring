"""研究任务的状态、租约和幂等治理。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.research.models import ResearchReport, ResearchTask
from app.research.reporting import ResearchReportDraft, validate_report_draft

TASK_ADMIN_ROLES = {"risk_admin", "platform_admin"}
DEFAULT_RESEARCH_BUDGET: dict[str, object] = {
    "max_queries": 5,
    "max_results": 50,
    "max_pages": 20,
    "max_input_tokens": 20_000,
    "max_output_tokens": 5_000,
}


class ResearchBudgetExceeded(RuntimeError):
    """研究任务预计用量超过预算快照。"""


def create_task(
    session: Session,
    *,
    owner_user_id: int,
    task_type: str,
    topic: str,
    supplier_scope: list[int],
    idempotency_key: str | None,
    budget_snapshot: dict[str, object] | None = None,
) -> ResearchTask:
    """创建任务；相同用户和幂等键重复提交返回原任务。"""
    if idempotency_key:
        existing = session.scalar(
            select(ResearchTask).where(
                ResearchTask.owner_user_id == owner_user_id,
                ResearchTask.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
    task = ResearchTask(
        owner_user_id=owner_user_id,
        task_type=task_type,
        topic=topic.strip(),
        supplier_scope=sorted(set(supplier_scope)),
        budget_snapshot=dict(budget_snapshot or DEFAULT_RESEARCH_BUDGET),
        idempotency_key=idempotency_key,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            existing = session.scalar(
                select(ResearchTask).where(
                    ResearchTask.owner_user_id == owner_user_id,
                    ResearchTask.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
        raise
    session.refresh(task)
    return task


def list_tasks(
    session: Session, *, owner_user_id: int, role: str
) -> list[ResearchTask]:
    stmt = select(ResearchTask).order_by(ResearchTask.created_at.desc())
    if role not in TASK_ADMIN_ROLES:
        stmt = stmt.where(ResearchTask.owner_user_id == owner_user_id)
    return list(session.scalars(stmt))


def get_task(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> ResearchTask | None:
    task = session.get(ResearchTask, task_id)
    if task is None:
        return None
    if role not in TASK_ADMIN_ROLES and task.owner_user_id != owner_user_id:
        return None
    return task


def cancel_task(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> ResearchTask | None:
    task = get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role)
    if task is None:
        return None
    if task.status in {"succeeded", "failed", "cancelled"}:
        return task
    now = datetime.now(UTC)
    if task.status == "queued":
        task.status = "cancelled"
        task.finished_at = now
    elif task.cancel_requested_at is None:
        task.cancel_requested_at = now
    session.commit()
    session.refresh(task)
    return task


def claim_next_task(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ResearchTask | None:
    """以行锁认领一个任务；租约过期的 running 任务可恢复。"""
    current = now or datetime.now(UTC)
    stmt = (
        select(ResearchTask)
        .where(
            ResearchTask.cancel_requested_at.is_(None),
            or_(
                ResearchTask.status == "queued",
                (
                    (ResearchTask.status == "running")
                    & (ResearchTask.lease_until <= current)
                ),
            ),
        )
        .order_by(ResearchTask.created_at, ResearchTask.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = session.scalars(stmt).first()
    if task is None:
        return None
    task.status = "running"
    task.worker_id = worker_id
    task.lease_until = current + timedelta(seconds=lease_seconds)
    task.attempts += 1
    task.started_at = task.started_at or current
    session.commit()
    session.refresh(task)
    return task


def renew_task_lease(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ResearchTask | None:
    """延长当前 Worker 的有效租约；过期租约不可自助复活。"""
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    task.lease_until = current + timedelta(seconds=lease_seconds)
    session.commit()
    session.refresh(task)
    return task


def record_task_usage(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    search_queries_delta: int = 0,
    search_results_delta: int = 0,
    input_tokens_delta: int = 0,
    output_tokens_delta: int = 0,
    cost_delta: Decimal = Decimal("0"),
    current_step: str | None = None,
    now: datetime | None = None,
) -> ResearchTask | None:
    """记录当前 Worker 的增量用量；租约失效后拒绝旁路写入。"""
    deltas = (
        search_queries_delta,
        search_results_delta,
        input_tokens_delta,
        output_tokens_delta,
    )
    if any(delta < 0 for delta in deltas) or cost_delta < 0:
        raise ValueError("任务用量增量不能为负数")
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    task.search_queries_used += search_queries_delta
    task.search_results_used += search_results_delta
    task.input_tokens_used += input_tokens_delta
    task.output_tokens_used += output_tokens_delta
    task.cost_amount += cost_delta
    if current_step is not None:
        task.current_step = current_step.strip() or None
    session.commit()
    session.refresh(task)
    return task


def reserve_task_usage(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    search_queries_delta: int = 0,
    search_results_delta: int = 0,
    input_tokens_delta: int = 0,
    output_tokens_delta: int = 0,
    cost_delta: Decimal = Decimal("0"),
    current_step: str | None = None,
    now: datetime | None = None,
) -> ResearchTask | None:
    """预留一组用量；超预算时原子拒绝，不写入部分计数。"""
    deltas = (
        search_queries_delta,
        search_results_delta,
        input_tokens_delta,
        output_tokens_delta,
    )
    if any(delta < 0 for delta in deltas) or cost_delta < 0:
        raise ValueError("任务用量增量不能为负数")
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None

    projected_queries = task.search_queries_used + search_queries_delta
    projected_results = task.search_results_used + search_results_delta
    projected_input_tokens = task.input_tokens_used + input_tokens_delta
    projected_output_tokens = task.output_tokens_used + output_tokens_delta
    projected_cost = task.cost_amount + cost_delta
    projected = (
        ("max_queries", Decimal(projected_queries)),
        ("max_results", Decimal(projected_results)),
        ("max_input_tokens", Decimal(projected_input_tokens)),
        ("max_output_tokens", Decimal(projected_output_tokens)),
        ("max_cost", projected_cost),
    )
    for key, value in projected:
        raw_limit = task.budget_snapshot.get(key)
        if raw_limit is None:
            continue
        try:
            limit = Decimal(str(raw_limit))
        except Exception as exc:  # noqa: BLE001 - 损坏预算必须安全拒绝
            raise ResearchBudgetExceeded(f"预算配置无效：{key}") from exc
        if limit < 0 or value > limit:
            raise ResearchBudgetExceeded(f"研究任务超过预算：{key}")

    task.search_queries_used = projected_queries
    task.search_results_used = projected_results
    task.input_tokens_used = projected_input_tokens
    task.output_tokens_used = projected_output_tokens
    task.cost_amount = projected_cost
    if current_step is not None:
        task.current_step = current_step.strip() or None
    session.commit()
    session.refresh(task)
    return task


def complete_task(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    succeeded: bool,
    error: str | None = None,
    now: datetime | None = None,
) -> ResearchTask | None:
    """仅当前 Worker 可以完成自己的租约；取消请求优先转为 cancelled。"""
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
        )
    )
    if task is None:
        return None
    task.status = (
        "cancelled"
        if task.cancel_requested_at
        else ("succeeded" if succeeded else "failed")
    )
    task.error = error[:1000] if error else None
    task.finished_at = now or datetime.now(UTC)
    task.worker_id = None
    task.lease_until = None
    session.commit()
    session.refresh(task)
    return task


def create_report(
    session: Session,
    *,
    task_id: int,
    owner_user_id: int,
    role: str,
    draft: ResearchReportDraft,
    model_version: str | None,
) -> ResearchReport | None:
    """保存通过结构化校验的报告草稿；不改变任务状态或风险信号。"""
    task = get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role)
    if task is None:
        return None
    validate_report_draft(draft)
    payload = draft.model_dump(mode="json")
    report = ResearchReport(
        task_id=task_id,
        title=draft.title,
        draft_payload=payload,
        status="draft",
        review_status="pending",
        model_version=model_version,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def list_reports(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> list[ResearchReport] | None:
    """按任务读取报告草稿，并沿用任务创建者/管理员隔离规则。"""
    if get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role) is None:
        return None
    stmt = (
        select(ResearchReport)
        .where(ResearchReport.task_id == task_id)
        .order_by(ResearchReport.created_at.desc(), ResearchReport.id.desc())
    )
    return list(session.scalars(stmt))
