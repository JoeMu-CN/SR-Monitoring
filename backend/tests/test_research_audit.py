"""阶段 0：研究任务生命周期审计。"""

from sqlalchemy import select

from app.auth.models import SecurityAuditEvent


def _report_payload() -> dict[str, object]:
    return {
        "model_version": "audit-test-v1",
        "draft": {
            "title": "审计研究草稿",
            "disclaimer": "AI 生成，仅供参考，不作为重大决策核心依据。",
            "facts": [
                {
                    "claim_id": "fact-audit-1",
                    "claim_type": "fact",
                    "text": "官方公告披露了供应链调整信息。",
                    "citation_ids": ["citation-audit-1"],
                    "confidence": 90,
                }
            ],
            "inferences": [],
            "forecasts": [],
            "citations": [
                {
                    "citation_id": "citation-audit-1",
                    "url": "https://official.example/audit",
                    "quote": "官方公告披露了供应链调整信息",
                    "verified": True,
                }
            ],
        },
    }


def test_research_task_create_cancel_and_report_write_audit_events(
    client, db_session, auth_as
) -> None:
    user = auth_as("risk_analyst", "research-audit-owner")
    topic = "不应写入审计详情的内部主题"
    created = client.post("/api/v1/research/tasks", json={"topic": topic})
    assert created.status_code == 202
    task_id = created.json()["id"]

    cancelled = client.post(f"/api/v1/research/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200

    report = client.post(
        f"/api/v1/research/tasks/{task_id}/reports",
        json=_report_payload(),
    )
    assert report.status_code == 202

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(SecurityAuditEvent.actor_user_id == user.id)
            .order_by(SecurityAuditEvent.id)
        )
    )
    actions = [event.action for event in events]
    assert "research_task_created" in actions
    assert "research_task_cancelled" in actions
    assert "research_report_draft_created" in actions
    assert all(topic not in (event.detail or "") for event in events)
    assert all("token" not in (event.detail or "").lower() for event in events)


def test_research_audit_query_is_admin_only_and_minimal(client, auth_as) -> None:
    auth_as("risk_analyst", "research-audit-query-owner")
    created = client.post(
        "/api/v1/research/tasks",
        json={"topic": "不应出现在审计查询响应中的主题"},
    )
    assert created.status_code == 202

    assert client.get("/api/v1/research/audit-logs").status_code == 403

    auth_as("viewer", "research-audit-query-viewer")
    assert client.get("/api/v1/research/audit-logs").status_code == 403

    auth_as("risk_admin", "research-audit-query-admin")
    response = client.get("/api/v1/research/audit-logs?limit=10&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["limit"] == 10
    assert body["offset"] == 0
    event = next(
        item for item in body["items"] if item["action"] == "research_task_created"
    )
    assert event["resource_type"] == "research_task"
    assert "不应出现在审计查询响应中的主题" not in str(event)
    assert "request_id" not in event
    assert "source_ip_masked" not in event
