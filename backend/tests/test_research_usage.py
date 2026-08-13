"""阶段 0：研究任务预算快照与执行计量。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.research.service import (
    claim_next_task,
    create_task,
    record_task_usage,
)


def test_task_has_default_budget_snapshot_and_zero_usage(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-usage-defaults")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="预算默认值",
        supplier_scope=[],
        idempotency_key=None,
    )

    assert task.budget_snapshot["max_queries"] == 5
    assert task.budget_snapshot["max_results"] == 50
    assert task.search_queries_used == 0
    assert task.search_results_used == 0
    assert task.input_tokens_used == 0
    assert task.output_tokens_used == 0
    assert task.cost_amount == Decimal("0")
    assert task.current_step is None


def test_current_worker_can_record_usage_with_step(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-usage-record")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="记录用量",
        supplier_scope=[],
        idempotency_key=None,
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(
        db_session,
        worker_id="worker-usage",
        lease_seconds=60,
        now=now,
    ) is not None

    updated = record_task_usage(
        db_session,
        task_id=task.id,
        worker_id="worker-usage",
        search_queries_delta=2,
        search_results_delta=8,
        input_tokens_delta=1200,
        output_tokens_delta=300,
        cost_delta=Decimal("0.12500000"),
        current_step="引用回验",
        now=now + timedelta(seconds=10),
    )

    assert updated is not None
    assert updated.search_queries_used == 2
    assert updated.search_results_used == 8
    assert updated.input_tokens_used == 1200
    assert updated.output_tokens_used == 300
    assert updated.cost_amount == Decimal("0.12500000")
    assert updated.current_step == "引用回验"


def test_wrong_or_expired_worker_cannot_record_usage(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-usage-owner")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="拒绝旁路记账",
        supplier_scope=[],
        idempotency_key=None,
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(
        db_session,
        worker_id="worker-valid",
        lease_seconds=60,
        now=now,
    ) is not None

    assert record_task_usage(
        db_session,
        task_id=task.id,
        worker_id="worker-other",
        search_queries_delta=1,
        now=now + timedelta(seconds=10),
    ) is None
    assert record_task_usage(
        db_session,
        task_id=task.id,
        worker_id="worker-valid",
        search_queries_delta=1,
        now=now + timedelta(seconds=61),
    ) is None


def test_usage_rejects_negative_deltas(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-usage-negative")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="负数用量",
        supplier_scope=[],
        idempotency_key=None,
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(
        db_session,
        worker_id="worker-negative",
        lease_seconds=60,
        now=now,
    ) is not None

    with pytest.raises(ValueError, match="不能为负数"):
        record_task_usage(
            db_session,
            task_id=task.id,
            worker_id="worker-negative",
            output_tokens_delta=-1,
            now=now,
        )
