"""Scheduler 风险生命周期协调事务测试。"""

from contextlib import nullcontext
from datetime import UTC, datetime

from pytest import MonkeyPatch
from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy.orm import Session

import app.scheduler.validity_job as validity_job

NOW_UTC = datetime(2026, 9, 6, 12, tzinfo=UTC)


def test_scheduler_expires_review_due_signal_event_and_alert_together(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given
    risk = linked_risk(
        db_session,
        (
            SignalSpec(
                "scheduler-review-due",
                review_due_at=NOW_UTC,
                mode="until_revoked",
            ),
        ),
        now_utc=NOW_UTC,
    )
    monkeypatch.setattr(validity_job, "SessionLocal", lambda: nullcontext(db_session))

    # When
    validity_job.risk_validity_job(now_utc=NOW_UTC)

    # Then
    assert risk.signals[0].validity_state == "expired"
    assert risk.event.validity_state == "expired"
    assert risk.alert.status == "expired"


def test_scheduler_legacy_alert_uses_stored_deadline_boundary(
    db_session: Session, monkeypatch: MonkeyPatch
) -> None:
    # Given
    risk = linked_risk(
        db_session,
        (SignalSpec("scheduler-legacy", state="expired", valid_until=NOW_UTC),),
        now_utc=NOW_UTC,
    )
    risk.event.validity_state = "legacy"
    risk.event.validity_policy_version = None
    risk.event.validity_reason = {
        "code": "legacy_unmigrated",
        "anchor_source": "legacy",
        "details": {},
    }
    risk.alert.expiry_kind = "legacy"
    risk.alert.expires_at = NOW_UTC
    monkeypatch.setattr(validity_job, "SessionLocal", lambda: nullcontext(db_session))

    # When
    validity_job.risk_validity_job(now_utc=NOW_UTC)

    # Then
    assert risk.event.validity_state == "legacy"
    assert risk.alert.status == "expired"
    assert risk.alert.expiry_kind == "legacy"
