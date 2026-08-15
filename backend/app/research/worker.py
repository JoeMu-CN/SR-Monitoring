"""研究任务的单次 Worker 编排骨架。

本模块只负责任务租约和执行结果回写；搜索、网页读取、模型调用等能力由后续
切片以显式执行器注入，避免在 Worker 基础设施中偷偷产生外部访问或费用。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from app.auth.security import write_audit
from app.research.models import ResearchTask
from app.research.service import claim_next_task, complete_task

TaskExecutor = Callable[[Session, ResearchTask], None]


def run_once(
    session: Session,
    *,
    worker_id: str,
    execute: TaskExecutor,
    lease_seconds: int = 300,
    task_type: str | None = None,
    topic_prefix: str | None = None,
    require_source_urls: bool = False,
    require_execution_requested: bool = False,
    now: datetime | None = None,
) -> ResearchTask | None:
    """认领并执行一个任务；没有可执行任务时返回 ``None``。

    执行器异常只记录异常类型，不把异常正文写入任务错误字段，避免意外泄露
    凭据或网页正文；任务仍会按失败状态完成回写。长任务的续租/心跳由后续
    Worker 切片补充。
    """

    task = claim_next_task(
        session,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        task_type=task_type,
        topic_prefix=topic_prefix,
        require_source_urls=require_source_urls,
        require_execution_requested=require_execution_requested,
        now=now,
    )
    if task is None:
        return None

    write_audit(
        session,
        action="research_task_claimed",
        resource_type="research_task",
        resource_id=str(task.id),
        detail=f"worker_id={worker_id}",
    )
    session.commit()

    succeeded = True
    error: str | None = None
    try:
        execute(session, task)
    except Exception as exc:  # noqa: BLE001 - 任务必须落为失败而非遗留 running
        succeeded = False
        error = f"executor_error:{type(exc).__name__}"

    completed = complete_task(
        session,
        task_id=task.id,
        worker_id=worker_id,
        succeeded=succeeded,
        error=error,
        now=now,
    )
    if completed is not None:
        result_action = {
            "succeeded": "research_task_succeeded",
            "failed": "research_task_failed",
            "cancelled": "research_task_cancelled",
        }[completed.status]
        write_audit(
            session,
            action=result_action,
            resource_type="research_task",
            resource_id=str(completed.id),
            detail=f"worker_id={worker_id};status={completed.status}",
        )
        session.commit()
    return completed
