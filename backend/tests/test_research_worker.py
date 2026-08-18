"""阶段 0：研究 Worker 单次执行骨架。"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.auth.models import SecurityAuditEvent
from app.research.models import ResearchBatch
from app.research.service import (
    ResearchBudgetExceeded,
    cancel_task,
    create_task,
)
from app.research.worker import run_once
from app.suppliers.models import Supplier


def test_worker_returns_none_when_queue_is_empty(db_session, auth_as) -> None:
    auth_as("risk_admin", "research-worker-empty")
    called = False

    def execute(*_args) -> None:
        nonlocal called
        called = True

    assert run_once(db_session, worker_id="worker-empty", execute=execute) is None
    assert called is False


def test_worker_completes_successful_execution(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-worker-success")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="单次执行成功",
        supplier_scope=[],
        idempotency_key=None,
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    observed: list[tuple[int, str]] = []

    def execute(_session, claimed) -> None:
        observed.append((claimed.id, claimed.status))

    completed = run_once(
        db_session,
        worker_id="worker-success",
        execute=execute,
        lease_seconds=60,
        now=now,
    )

    assert completed is not None
    assert completed.id == task.id
    assert completed.status == "succeeded"
    assert completed.attempts == 1
    assert observed == [(task.id, "running")]

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.resource_type == "research_task",
                SecurityAuditEvent.resource_id == str(task.id),
            )
            .order_by(SecurityAuditEvent.id)
        )
    )
    assert [event.action for event in events] == [
        "research_task_claimed",
        "research_task_succeeded",
    ]
    assert all("worker-success" in (event.detail or "") for event in events)


def test_worker_converts_executor_exception_to_failed_without_message_leak(
    db_session, auth_as
) -> None:
    user = auth_as("risk_admin", "research-worker-failure")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="单次执行失败",
        supplier_scope=[],
        idempotency_key=None,
    )

    def execute(*_args) -> None:
        raise RuntimeError("secret-token-must-not-be-stored")

    completed = run_once(db_session, worker_id="worker-failure", execute=execute)

    assert completed is not None
    assert completed.status == "failed"
    assert completed.error == "executor_error:RuntimeError"
    assert "secret-token" not in (completed.error or "")
    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.resource_type == "research_task",
                SecurityAuditEvent.resource_id == str(task.id),
            )
            .order_by(SecurityAuditEvent.id)
        )
    )
    assert [event.action for event in events] == [
        "research_task_claimed",
        "research_task_failed",
    ]
    assert all("secret-token" not in (event.detail or "") for event in events)


def test_worker_honors_cancel_requested_during_execution(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-worker-cancel")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="执行期间取消",
        supplier_scope=[],
        idempotency_key=None,
    )

    def execute(session, claimed) -> None:
        requested = cancel_task(
            session,
            task_id=claimed.id,
            owner_user_id=user.id,
            role="risk_admin",
        )
        assert requested is not None
        assert requested.status == "running"
        assert requested.cancel_requested_at is not None

    completed = run_once(db_session, worker_id="worker-cancel", execute=execute)

    assert completed is not None
    assert completed.id == task.id
    assert completed.status == "cancelled"
    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.resource_type == "research_task",
                SecurityAuditEvent.resource_id == str(task.id),
            )
            .order_by(SecurityAuditEvent.id)
        )
    )
    assert [event.action for event in events] == [
        "research_task_claimed",
        "research_task_cancelled",
    ]


def test_periodic_worker_skips_supplier_disabled_before_execution(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-worker-skipped")
    supplier = Supplier(
        supplier_code="WORKER-SKIP-001",
        legal_name="执行前停用供应商",
        country_code="CN",
        enabled=True,
    )
    db_session.add(supplier)
    db_session.flush()
    batch = ResearchBatch(
        owner_user_id=user.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=datetime(2026, 8, 1).date(),
        period_end=datetime(2026, 8, 31).date(),
        supplier_snapshot=[
            {"supplier_id": supplier.id, "supplier_code": supplier.supplier_code}
        ],
        supplier_count=1,
        queued_count=1,
    )
    db_session.add(batch)
    db_session.flush()
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="monthly",
        topic="停用供应商跳过",
        supplier_scope=[supplier.id],
        idempotency_key="worker-skip-task",
        batch_id=batch.id,
    )
    supplier.enabled = False
    db_session.commit()
    called = False

    def execute(*_args) -> None:
        nonlocal called
        called = True

    completed = run_once(db_session, worker_id="worker-skipped", execute=execute)

    assert completed is not None and completed.id == task.id
    assert completed.status == "skipped"
    assert completed.error == "skipped:supplier_disabled"
    assert called is False
    assert db_session.get(ResearchBatch, batch.id).status == "partial"


def test_worker_marks_budget_exhausted_separately_from_failure(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-worker-budget")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="预算耗尽终态",
        supplier_scope=[],
        idempotency_key=None,
    )

    def execute(*_args) -> None:
        raise ResearchBudgetExceeded("不应把正文写入 error")

    completed = run_once(db_session, worker_id="worker-budget", execute=execute)

    assert completed is not None and completed.id == task.id
    assert completed.status == "budget_exhausted"
    assert completed.error == "budget_exhausted:ResearchBudgetExceeded"
    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.resource_type == "research_task",
                SecurityAuditEvent.resource_id == str(task.id),
            )
            .order_by(SecurityAuditEvent.id)
        )
    )
    assert [event.action for event in events] == [
        "research_task_claimed",
        "research_task_budget_exhausted",
    ]
