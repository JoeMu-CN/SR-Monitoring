"""研究报告草稿 API 测试。"""


def report_payload(*, verified: bool = True) -> dict[str, object]:
    return {
        "model_version": "contract-test-v1",
        "draft": {
            "title": "供应链公开信息研究",
            "disclaimer": "AI 生成，仅供参考，不作为重大决策核心依据。",
            "facts": [
                {
                    "claim_id": "fact-1",
                    "claim_type": "fact",
                    "text": "官方公告披露了供应链调整信息。",
                    "citation_ids": ["c-1"],
                    "confidence": 90,
                }
            ],
            "inferences": [],
            "forecasts": [],
            "citations": [
                {
                    "citation_id": "c-1",
                    "url": "https://official.example/final",
                    "quote": "官方公告披露了供应链调整信息",
                    "verified": verified,
                }
            ],
        },
    }


def test_report_draft_create_and_list(client, auth_as) -> None:
    auth_as("risk_analyst", "report-owner")
    task = client.post("/api/v1/research/tasks", json={"topic": "公开研究主题"})
    task_id = task.json()["id"]

    created = client.post(
        f"/api/v1/research/tasks/{task_id}/reports",
        json=report_payload(),
    )
    assert created.status_code == 202
    assert created.json()["title"] == "供应链公开信息研究"
    assert created.json()["review_status"] == "pending"
    assert created.json()["draft"]["facts"][0]["claim_id"] == "fact-1"

    listed = client.get(f"/api/v1/research/tasks/{task_id}/reports")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


def test_report_draft_rejects_unverified_citation(client, auth_as) -> None:
    auth_as("risk_analyst", "report-unverified")
    task = client.post("/api/v1/research/tasks", json={"topic": "公开研究主题"})
    task_id = task.json()["id"]

    response = client.post(
        f"/api/v1/research/tasks/{task_id}/reports",
        json=report_payload(verified=False),
    )
    assert response.status_code == 422
    assert "没有经过回验" in response.json()["detail"]


def test_report_draft_enforces_task_owner_isolation(client, auth_as) -> None:
    auth_as("risk_analyst", "report-owner-a")
    task = client.post("/api/v1/research/tasks", json={"topic": "隔离主题"})
    task_id = task.json()["id"]

    auth_as("risk_analyst", "report-owner-b")
    assert (
        client.post(
            f"/api/v1/research/tasks/{task_id}/reports",
            json=report_payload(),
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/research/tasks/{task_id}/reports").status_code == 404


def test_viewer_cannot_create_report_task(client, auth_as) -> None:
    auth_as("viewer", "report-viewer")
    response = client.post(
        "/api/v1/research/tasks/1/reports",
        json=report_payload(),
    )
    assert response.status_code == 403
