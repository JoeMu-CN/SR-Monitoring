"""阶段 0：研究任务持久化、权限、幂等和租约治理。"""

from datetime import UTC, datetime, timedelta

from app.research.service import claim_next_task, complete_task, create_task


def test_research_task_creation_is_idempotent_and_returns_accepted(client, auth_as) -> None:
    auth_as("risk_analyst", "research-owner-idempotent")
    payload = {
        "task_type": "manual",
        "topic": "某供应商近 30 天公开风险动态",
        "supplier_scope": [3, 1, 3],
        "idempotency_key": "manual-2026-08-11-001",
    }

    first = client.post("/api/v1/research/tasks", json=payload)
    second = client.post("/api/v1/research/tasks", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["supplier_scope"] == [1, 3]
    assert first.json()["status"] == "queued"


def test_manual_api_creates_immediate_task_and_rejects_scheduled_types(client) -> None:
    created = client.post(
        "/api/v1/research/tasks",
        json={"topic": "管理员即时研究"},
    )
    rejected = client.post(
        "/api/v1/research/tasks",
        json={"task_type": "daily", "topic": "不得从浏览器伪造日报"},
    )

    assert created.status_code == 202
    assert created.json()["task_type"] == "manual"
    assert created.json()["status"] == "queued"
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "daily/weekly 任务只能由 Scheduler 创建"


def test_research_task_requires_analyst_and_owner_isolation(client, auth_as) -> None:
    auth_as("viewer", "research-viewer")
    forbidden = client.post(
        "/api/v1/research/tasks",
        json={"topic": "不应创建"},
    )
    assert forbidden.status_code == 403

    owner = auth_as("risk_analyst", "research-owner-a")
    created = client.post(
        "/api/v1/research/tasks",
        json={"topic": "供应链公开信息"},
    )
    task_id = created.json()["id"]
    assert created.status_code == 202
    assert client.get(f"/api/v1/research/tasks/{task_id}").status_code == 200

    auth_as("risk_analyst", "research-owner-b")
    assert client.get(f"/api/v1/research/tasks/{task_id}").status_code == 404

    auth_as("risk_admin", "research-admin")
    listed = client.get("/api/v1/research/tasks")
    assert listed.status_code == 200
    assert any(
        item["id"] == task_id and item["owner_user_id"] == owner.id
        for item in listed.json()["items"]
    )


def test_cancel_is_idempotent_and_running_cancel_waits_for_worker(
    db_session, client, auth_as
) -> None:
    user = auth_as("risk_analyst", "research-cancel-owner")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="取消测试",
        supplier_scope=[],
        idempotency_key=None,
    )
    response = client.post(f"/api/v1/research/tasks/{task.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    repeated = client.post(f"/api/v1/research/tasks/{task.id}/cancel")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"

    running = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="运行取消测试",
        supplier_scope=[],
        idempotency_key=None,
    )
    claimed = claim_next_task(
        db_session,
        worker_id="worker-a",
        lease_seconds=60,
        now=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert claimed is not None and claimed.id == running.id
    response = client.post(f"/api/v1/research/tasks/{running.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    completed = complete_task(
        db_session,
        task_id=running.id,
        worker_id="worker-a",
        succeeded=True,
        now=datetime(2026, 8, 11, 0, 1, tzinfo=UTC),
    )
    assert completed is not None and completed.status == "cancelled"


def test_worker_lease_reclaims_expired_task_without_double_claim(
    db_session, client, auth_as
) -> None:
    user = auth_as("risk_admin", "research-worker-owner")
    task = create_task(
        db_session,
        owner_user_id=user.id,
        task_type="manual",
        topic="租约测试",
        supplier_scope=[],
        idempotency_key=None,
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    first = claim_next_task(db_session, worker_id="worker-a", lease_seconds=60, now=now)
    assert first is not None and first.id == task.id and first.attempts == 1
    assert claim_next_task(db_session, worker_id="worker-b", now=now) is None

    reclaimed = claim_next_task(
        db_session,
        worker_id="worker-b",
        lease_seconds=60,
        now=now + timedelta(seconds=61),
    )
    assert reclaimed is not None and reclaimed.id == task.id
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.attempts == 2
    assert complete_task(
        db_session,
        task_id=task.id,
        worker_id="worker-a",
        succeeded=True,
    ) is None
    completed = complete_task(
        db_session,
        task_id=task.id,
        worker_id="worker-b",
        succeeded=True,
    )
    assert completed is not None and completed.status == "succeeded"
