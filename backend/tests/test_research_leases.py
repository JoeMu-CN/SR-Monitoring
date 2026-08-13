"""阶段 0：研究任务租约续租边界。"""

from datetime import UTC, datetime, timedelta

from app.research.service import claim_next_task, create_task, renew_task_lease


def test_current_worker_can_renew_an_unexpired_lease(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-lease-renew")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="有效续租",
        supplier_scope=[],
        idempotency_key=None,
    )
    start = datetime(2026, 8, 11, tzinfo=UTC)
    claimed = claim_next_task(
        db_session,
        worker_id="worker-a",
        lease_seconds=60,
        now=start,
    )
    assert claimed is not None and claimed.id == task.id

    renewed = renew_task_lease(
        db_session,
        task_id=task.id,
        worker_id="worker-a",
        lease_seconds=120,
        now=start + timedelta(seconds=30),
    )

    assert renewed is not None
    assert renewed.lease_until == start + timedelta(seconds=150)


def test_wrong_worker_cannot_renew_a_valid_lease(db_session, auth_as) -> None:
    user = auth_as("risk_admin", "research-lease-owner")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="错误 Worker 续租",
        supplier_scope=[],
        idempotency_key=None,
    )
    start = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(
        db_session,
        worker_id="worker-a",
        lease_seconds=60,
        now=start,
    ) is not None

    assert renew_task_lease(
        db_session,
        task_id=task.id,
        worker_id="worker-b",
        now=start + timedelta(seconds=30),
    ) is None


def test_expired_worker_cannot_renew_but_another_worker_can_reclaim(
    db_session, auth_as
) -> None:
    user = auth_as("risk_admin", "research-lease-expired")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="过期租约",
        supplier_scope=[],
        idempotency_key=None,
    )
    start = datetime(2026, 8, 11, tzinfo=UTC)
    assert claim_next_task(
        db_session,
        worker_id="worker-a",
        lease_seconds=60,
        now=start,
    ) is not None

    expired_at = start + timedelta(seconds=61)
    assert renew_task_lease(
        db_session,
        task_id=task.id,
        worker_id="worker-a",
        now=expired_at,
    ) is None
    reclaimed = claim_next_task(
        db_session,
        worker_id="worker-b",
        lease_seconds=60,
        now=expired_at,
    )

    assert reclaimed is not None
    assert reclaimed.id == task.id
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.attempts == 2
