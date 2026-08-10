"""Phase 2 会话授权、旧 Header 失效与 Agent 所有权测试。"""

from app.agent.models import AgentSession, SourceOnboardingDraft


def test_business_routes_require_session_and_ignore_forged_role(client) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    forged = {"X-User-Role": "admin", "X-User-Id": "forged-admin"}

    for path in (
        "/api/v1/suppliers",
        "/api/v1/sources",
        "/api/v1/dashboard/summary",
        "/api/v1/rule-engine/dimensions",
        "/api/v1/agent/status",
    ):
        assert client.get(path, headers=forged).status_code == 401


def test_viewer_reads_summaries_but_cannot_use_admin_capabilities(
    client, auth_as
) -> None:
    auth_as("viewer", "permission-viewer")

    assert client.get("/api/v1/suppliers").status_code == 200
    sources = client.get("/api/v1/sources")
    assert sources.status_code == 200
    assert client.get("/api/v1/rule-engine/dimensions").status_code == 200
    assert client.get("/api/v1/agent/status").status_code == 403
    assert (
        client.get(
            "/api/v1/sources/admin", headers={"X-User-Role": "admin"}
        ).status_code
        == 403
    )

    sensitive = {
        "endpoint_url",
        "auth_type",
        "login_config",
        "credential_ref",
        "api_key_configured",
        "api_key_hint",
        "adapter_config",
    }
    assert sources.json()
    assert all(sensitive.isdisjoint(item) for item in sources.json())


def test_risk_analyst_and_risk_admin_permissions(client, auth_as) -> None:
    auth_as("risk_analyst", "permission-analyst")
    assert client.get("/api/v1/agent/status").status_code == 200
    assert client.get("/api/v1/sources/admin").status_code == 403

    auth_as("risk_admin", "permission-risk-admin")
    assert client.get("/api/v1/sources/admin").status_code == 200
    assert client.get("/api/v1/sources/audit-logs").status_code == 200


def test_business_write_requires_csrf(client) -> None:
    client.headers.pop("X-CSRF-Token", None)
    response = client.post(
        "/api/v1/risk-alerts/expire",
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 403


def test_agent_session_and_draft_are_isolated_by_owner(
    client, db_session, auth_as
) -> None:
    owner_a = auth_as("risk_admin", "agent-owner-a")
    risk_session = AgentSession(
        agent_kind="risk_query", owner_user_id=owner_a.id
    )
    source_session_a = AgentSession(
        agent_kind="source_onboarding", owner_user_id=owner_a.id
    )
    db_session.add_all([risk_session, source_session_a])
    db_session.flush()
    draft_a = SourceOnboardingDraft(
        agent_session_id=source_session_a.id,
        actor_id=str(owner_a.id),
        current_step="collection_goal",
        answers={"source_url": "https://owner-a.example/source"},
    )
    db_session.add(draft_a)
    db_session.commit()

    owner_b = auth_as("risk_admin", "agent-owner-b")
    source_session_b = AgentSession(
        agent_kind="source_onboarding", owner_user_id=owner_b.id
    )
    db_session.add(source_session_b)
    db_session.flush()
    draft_b = SourceOnboardingDraft(
        agent_session_id=source_session_b.id,
        actor_id=str(owner_b.id),
        current_step="collection_goal",
        answers={"source_url": "https://owner-b.example/source"},
    )
    db_session.add(draft_b)
    db_session.commit()

    continued = client.post(
        "/api/v1/chat",
        json={"question": "继续", "session_id": risk_session.id},
        headers={"X-User-Id": str(owner_a.id)},
    )
    assert continued.status_code == 404

    box = client.get("/api/v1/source-agent/drafts")
    draft_ids = {
        item["draft_id"]
        for item in box.json()["items"]
        if item["kind"] == "in_progress"
    }
    assert draft_b.id in draft_ids
    assert draft_a.id not in draft_ids

    deleted = client.delete(
        f"/api/v1/source-agent/drafts/{draft_a.id}",
        headers={"X-User-Id": str(owner_a.id)},
    )
    assert deleted.status_code == 404
