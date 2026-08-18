"""研究任务执行事件的增量读取与权限隔离。"""


def test_task_events_support_incremental_cursor(client, auth_as) -> None:
    auth_as("risk_analyst", "research-event-owner")
    created = client.post(
        "/api/v1/research/tasks",
        json={"topic": "事件流测试"},
    )
    task_id = created.json()["id"]
    initial = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert initial.status_code == 200
    assert [item["event_type"] for item in initial.json()["items"]] == ["task_created"]
    cursor = initial.json()["next_after_id"]

    started = client.post(f"/api/v1/research/tasks/{task_id}/start")
    assert started.status_code == 200
    incremental = client.get(
        f"/api/v1/research/tasks/{task_id}/events",
        params={"after_id": cursor},
    )
    assert [item["event_type"] for item in incremental.json()["items"]] == [
        "execution_requested"
    ]
    assert incremental.json()["next_after_id"] > cursor


def test_task_events_follow_task_owner_isolation(client, auth_as) -> None:
    owner = auth_as("risk_analyst", "research-event-owner-a")
    created = client.post(
        "/api/v1/research/tasks",
        json={"topic": "事件权限测试"},
    )
    task_id = created.json()["id"]

    auth_as("risk_analyst", "research-event-owner-b")
    assert client.get(f"/api/v1/research/tasks/{task_id}/events").status_code == 404

    auth_as("risk_admin", "research-event-admin")
    response = client.get(f"/api/v1/research/tasks/{task_id}/events")
    assert response.status_code == 200
    assert response.json()["items"][0]["task_id"] == task_id
    assert response.json()["items"][0]["detail"] == {"task_type": "manual"}
    assert owner.id == created.json()["owner_user_id"]
