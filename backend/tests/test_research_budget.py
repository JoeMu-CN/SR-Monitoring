"""阶段 0：研究任务级预算预留闸门。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.research.service import (
    ResearchBudgetExceeded,
    claim_next_task,
    create_task,
    reserve_task_usage,
)


def test_reserve_usage_succeeds_within_snapshot(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-budget-reserve")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="预算内预留",
        supplier_scope=[],
        idempotency_key=None,
        budget_snapshot={
            "max_queries": 2,
            "max_results": 10,
            "max_input_tokens": 1000,
            "max_output_tokens": 500,
            "max_cost": "0.10000000",
        },
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(db_session, worker_id="worker-budget", now=now) is not None

    updated = reserve_task_usage(
        db_session,
        task_id=task.id,
        worker_id="worker-budget",
        search_queries_delta=1,
        search_results_delta=4,
        input_tokens_delta=600,
        output_tokens_delta=200,
        cost_delta=Decimal("0.05000000"),
        current_step="搜索",
        now=now + timedelta(seconds=5),
    )

    assert updated is not None
    assert updated.search_queries_used == 1
    assert updated.search_results_used == 4
    assert updated.input_tokens_used == 600
    assert updated.output_tokens_used == 200
    assert updated.cost_amount == Decimal("0.05000000")
    assert updated.current_step == "搜索"


def test_budget_exceeded_does_not_write_partial_usage(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-budget-exceeded")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="超预算",
        supplier_scope=[],
        idempotency_key=None,
        budget_snapshot={"max_queries": 1, "max_results": 2},
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(db_session, worker_id="worker-budget-limit", now=now) is not None

    with pytest.raises(ResearchBudgetExceeded, match="max_results"):
        reserve_task_usage(
            db_session,
            task_id=task.id,
            worker_id="worker-budget-limit",
            search_queries_delta=1,
            search_results_delta=3,
            now=now + timedelta(seconds=5),
        )

    db_session.refresh(task)
    assert task.search_queries_used == 0
    assert task.search_results_used == 0


def test_budget_gate_rejects_wrong_or_expired_worker(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-budget-owner")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="预算租约",
        supplier_scope=[],
        idempotency_key=None,
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(
        db_session,
        worker_id="worker-budget-owner",
        lease_seconds=60,
        now=now,
    ) is not None

    assert reserve_task_usage(
        db_session,
        task_id=task.id,
        worker_id="worker-other",
        search_queries_delta=1,
        now=now + timedelta(seconds=5),
    ) is None
    assert reserve_task_usage(
        db_session,
        task_id=task.id,
        worker_id="worker-budget-owner",
        search_queries_delta=1,
        now=now + timedelta(seconds=61),
    ) is None
