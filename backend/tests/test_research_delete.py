"""研究任务删除接口回归测试。"""

from sqlalchemy import select

from app.auth.models import SecurityAuditEvent
from app.research.models import ResearchSource, ResearchTask, ResearchTaskEvent


def test_delete_terminal_task_cascades_research_data_and_audits(
    client, db_session, auth_as
) -> None:
    user = auth_as("risk_analyst", "research-delete-owner")
    created = client.post("/api/v1/research/tasks", json={"topic": "待删除研究任务"})
    assert created.status_code == 202
    task_id = created.json()["id"]
    task = db_session.get(ResearchTask, task_id)
    assert task is not None
    db_session.add(
        ResearchSource(task_id=task_id, url="https://example.test/source", title="测试来源")
    )
    db_session.commit()

    cancelled = client.post(f"/api/v1/research/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    deleted = client.delete(f"/api/v1/research/tasks/{task_id}")
    assert deleted.status_code == 204
    db_session.expire_all()
    assert db_session.get(ResearchTask, task_id) is None
    assert db_session.scalar(
        select(ResearchTaskEvent).where(ResearchTaskEvent.task_id == task_id)
    ) is None
    assert db_session.scalar(
        select(ResearchSource).where(ResearchSource.task_id == task_id)
    ) is None
    audit = db_session.scalar(
        select(SecurityAuditEvent).where(
            SecurityAuditEvent.actor_user_id == user.id,
            SecurityAuditEvent.action == "research_task_deleted",
            SecurityAuditEvent.resource_id == str(task_id),
        )
    )
    assert audit is not None


def test_delete_task_is_owner_scoped_and_running_tasks_are_protected(
    client, db_session, auth_as
) -> None:
    auth_as("risk_analyst", "research-delete-owner-2")
    created = client.post("/api/v1/research/tasks", json={"topic": "权限删除测试"})
    assert created.status_code == 202
    task_id = created.json()["id"]

    auth_as("risk_analyst", "research-delete-other")
    assert client.delete(f"/api/v1/research/tasks/{task_id}").status_code == 404

    auth_as("risk_admin", "research-delete-admin")
    task = db_session.get(ResearchTask, task_id)
    assert task is not None
    task.status = "running"
    db_session.commit()
    protected = client.delete(f"/api/v1/research/tasks/{task_id}")
    assert protected.status_code == 409
    assert db_session.get(ResearchTask, task_id) is not None

    queued = client.post("/api/v1/research/tasks", json={"topic": "排队任务删除测试"})
    assert queued.status_code == 202
    queued_id = queued.json()["id"]
    assert client.delete(f"/api/v1/research/tasks/{queued_id}").status_code == 204
    assert db_session.get(ResearchTask, queued_id) is None
