from sqlalchemy import select

from app.signals.models import DataSource, DataSourceAuditLog


def test_source_console_requires_admin_and_audits_changes(client, db_session):
    payload = {
        "code": "custom-official-test",
        "name": "测试官方数据源",
        "source_type": "official_api",
        "credibility": 92,
        "schedule": "0 */6 * * *",
        "endpoint_url": "https://example.gov/api",
        "auth_type": "api_key",
        "credential_ref": "secret/data/custom-official-test",
        "api_key": "super-secret-value",
        "description": "仅用于接口契约测试",
        "enabled": False,
    }
    denied = client.post("/api/v1/sources", json=payload)
    assert denied.status_code == 403

    created = client.post(
        "/api/v1/sources",
        json=payload,
        headers={"X-User-Role": "admin", "X-User-Id": "test-admin"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["api_key_configured"] is True
    assert body["api_key_hint"] == "••••alue"
    assert "super-secret-value" not in created.text

    source_id = body["id"]
    updated = client.put(
        f"/api/v1/sources/{source_id}",
        json={"schedule": "*/30 * * * *", "enabled": False},
        headers={"X-User-Role": "admin", "X-User-Id": "test-admin"},
    )
    assert updated.status_code == 200
    assert updated.json()["schedule"] == "*/30 * * * *"
    assert updated.json()["enabled"] is False

    logs = client.get(f"/api/v1/sources/audit-logs?source_id={source_id}")
    assert logs.status_code == 200
    assert [item["action"] for item in logs.json()["items"]] == ["updated", "created"]
    assert all(item["actor_role"] == "admin" for item in logs.json()["items"])

    source = db_session.scalar(select(DataSource).where(DataSource.id == source_id))
    assert source is not None
    assert source.api_key_hash is not None
    assert source.api_key_last4 == "alue"
    assert db_session.scalar(
        select(DataSourceAuditLog).where(DataSourceAuditLog.source_id == source_id)
    ) is not None


def test_source_console_rejects_invalid_schedule(client):
    response = client.post(
        "/api/v1/sources",
        json={
            "code": "bad-schedule-test",
            "name": "非法周期",
            "source_type": "official",
            "credibility": 80,
            "schedule": "every day",
        },
        headers={"X-User-Role": "admin"},
    )
    assert response.status_code == 422


def test_tianyancha_source_reports_runtime_key_without_exposing_it(
    client, db_session, monkeypatch
):
    import app.config as config_module

    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None
    monkeypatch.setattr(config_module, "TYC_API_KEY", "tyc_runtime_secret")

    response = client.get("/api/v1/sources")
    assert response.status_code == 200
    tianyancha = next(item for item in response.json() if item["code"] == "tianyancha")
    assert tianyancha["source_type"] == "external_tool"
    assert tianyancha["api_key_configured"] is True
    assert tianyancha["api_key_hint"] == "环境变量已配置"
    assert "tyc_runtime_secret" not in response.text
