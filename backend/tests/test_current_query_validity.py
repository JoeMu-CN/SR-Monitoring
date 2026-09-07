"""任务 9：查询、统计与 Agent 工具共享实时判活口径。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.tools import QueryCurrentAlertsTool
from app.signals.validity import (
    AnchorSource,
    ValidityMode,
    ValidityProfile,
    ValidityState,
    ValidityWindow,
    is_signal_effective,
)


def test_past_due_current_alert_is_excluded_everywhere_but_history_keeps_signal(
    client: TestClient,
    db_session: Session,
) -> None:
    # Given
    now_utc = datetime.now(UTC)
    before_alerts = client.get("/api/v1/risk-alerts").json()["total"]
    before_dashboard = client.get("/api/v1/dashboard/summary").json()
    before_agent = asyncio.run(
        QueryCurrentAlertsTool(now_utc=now_utc).execute({}, db_session)
    )["total"]
    expired_at = now_utc - timedelta(days=1)
    risk = linked_risk(
        db_session,
        (SignalSpec("task-9-past-due", valid_until=expired_at),),
        now_utc=expired_at - timedelta(days=1),
    )
    risk.event.valid_until = expired_at
    risk.alert.expires_at = expired_at
    risk.alert.expiry_kind = "finite"
    db_session.commit()

    # When
    alerts = client.get("/api/v1/risk-alerts")
    dashboard = client.get("/api/v1/dashboard/summary")
    suppliers = client.get(
        "/api/v1/suppliers",
        params={"q": "TASK-8-task-9-past-due"},
    )
    agent = asyncio.run(
        QueryCurrentAlertsTool(now_utc=now_utc).execute({}, db_session)
    )
    valid_signals = client.get(
        f"/api/v1/sources/{risk.source.id}/signals",
        params={"scope": "valid"},
    )
    all_signals = client.get(
        f"/api/v1/sources/{risk.source.id}/signals",
        params={"scope": "all"},
    )

    # Then
    assert alerts.status_code == dashboard.status_code == suppliers.status_code == 200
    assert alerts.json()["total"] == before_alerts
    assert dashboard.json()["total_current"] == before_dashboard["total_current"]
    assert dashboard.json()["today_new"] == before_dashboard["today_new"]
    assert suppliers.json()["items"][0]["current_risk_level"] is None
    assert suppliers.json()["items"][0]["current_risk_score"] is None
    assert agent["total"] == before_agent
    assert valid_signals.json()["items"] == []
    assert [item["id"] for item in all_signals.json()["items"]] == [risk.signals[0].id]


def test_invalid_null_deadline_mode_is_not_effective_and_is_diagnosed_by_constraint(
    db_session: Session,
) -> None:
    # Given
    now_utc = datetime(2026, 9, 7, 12, tzinfo=UTC)
    invalid_window = ValidityWindow(
        profile=ValidityProfile.WEATHER_ALERT,
        mode=ValidityMode.FIXED_DAYS,
        state=ValidityState.ACTIVE,
        valid_from=now_utc,
        valid_until=None,
        review_due_at=None,
        anchor_source=AnchorSource.PUBLISHED_AT,
    )
    risk = linked_risk(
        db_session,
        (SignalSpec("task-9-invalid-alert", mode="until_revoked"),),
        now_utc=now_utc,
    )
    risk.alert.expiry_kind = "finite"

    # When / Then
    assert is_signal_effective(invalid_window, now_utc=now_utc) is False
    with pytest.raises(IntegrityError, match="ck_risk_alerts_expiry_value"):
        db_session.commit()


def test_current_query_boundaries_reject_invalid_scope_and_status(
    client: TestClient,
) -> None:
    # Given / When
    invalid_scope = client.get("/api/v1/sources/1/signals", params={"scope": "recent"})
    invalid_status = client.get("/api/v1/risk-alerts", params={"status": "pending"})

    # Then
    assert invalid_scope.status_code == 422
    assert invalid_status.status_code == 422
