"""事件与提醒有效期物化、生命周期联动及 legacy 切割测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy.orm import Session

from app.risks.validity import (
    expire_alerts,
    refresh_event_support,
)
from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    SignalValiditySnapshot,
    persist_signal_ingestions,
)
from app.signals.lifecycle import apply_lifecycle_action
from app.signals.schemas import ManualSignalInput
from app.signals.validity import LifecycleAction, ValidityProfile

NOW_UTC = datetime(2026, 9, 6, 12, tzinfo=UTC)


def test_event_and_alert_deadline_falls_back_as_support_is_revoked(
    db_session: Session,
) -> None:
    # Given
    day_3 = NOW_UTC + timedelta(days=3)
    day_7 = NOW_UTC + timedelta(days=7)
    risk = linked_risk(
        db_session,
        (
            SignalSpec("three-days", valid_until=day_3),
            SignalSpec("seven-days", valid_until=day_7),
            SignalSpec("unbounded", mode="indefinite"),
        ),
        now_utc=NOW_UTC,
    )

    # When / Then：任一无限证据使事件与提醒均为 unbounded。
    assert expire_alerts(db_session, now_utc=NOW_UTC) == 0
    assert risk.event.valid_until is None
    assert risk.event.review_due_at is None
    assert risk.alert.expires_at is None
    assert risk.alert.expiry_kind == "unbounded"

    # When / Then：逐条撤销后按剩余有效证据的最晚截止回落。
    risk.signals[2].validity_state = "revoked"
    expire_alerts(db_session, now_utc=NOW_UTC)
    assert risk.event.valid_until == day_7
    assert risk.alert.expires_at == day_7
    assert risk.alert.expiry_kind == "finite"
    risk.signals[1].validity_state = "revoked"
    expire_alerts(db_session, now_utc=NOW_UTC)
    assert risk.event.valid_until == day_3
    assert risk.alert.expires_at == day_3


@pytest.mark.parametrize(
    ("spec", "expected_state"),
    [
        (SignalSpec("pending", state="pending_classification"), "pending_classification"),
        (
            SignalSpec(
                "expired", state="expired", valid_until=NOW_UTC, mode="fixed_days"
            ),
            "expired",
        ),
        (
            SignalSpec("review-due", review_due_at=NOW_UTC, mode="until_revoked"),
            "expired",
        ),
    ],
)
def test_non_effective_evidence_never_keeps_current_alert(
    db_session: Session, spec: SignalSpec, expected_state: str
) -> None:
    # Given
    risk = linked_risk(db_session, (spec,), now_utc=NOW_UTC)

    # When
    expired = expire_alerts(db_session, now_utc=NOW_UTC)

    # Then
    assert expired == 1
    assert risk.signals[0].validity_state == expected_state
    assert risk.event.validity_state == "expired"
    assert risk.alert.status == "expired"


def test_materialized_deadline_does_not_drift_when_event_has_no_end_at(
    db_session: Session,
) -> None:
    # Given
    deadline = NOW_UTC + timedelta(days=3)
    risk = linked_risk(
        db_session,
        (SignalSpec("no-end-at", valid_until=deadline),),
        now_utc=NOW_UTC,
    )
    risk.event.end_at = None

    # When
    first = refresh_event_support(db_session, risk.event, now_utc=NOW_UTC)
    second = refresh_event_support(
        db_session, risk.event, now_utc=NOW_UTC + timedelta(days=1)
    )

    # Then
    assert first.expires_at == deadline
    assert second.expires_at == deadline
    assert risk.event.valid_until == deadline


def test_confirm_and_revoke_refresh_event_and_alert_in_same_transaction(
    db_session: Session,
) -> None:
    # Given
    day_3 = NOW_UTC + timedelta(days=3)
    day_7 = NOW_UTC + timedelta(days=7)
    risk = linked_risk(
        db_session,
        (SignalSpec("reviewed", review_due_at=day_3, mode="until_revoked"),),
        now_utc=NOW_UTC,
    )
    expire_alerts(db_session, now_utc=NOW_UTC)
    action = ManualSignalInput(
        external_id="confirm-reviewed",
        title="确认",
        content="确认",
        published_at=NOW_UTC,
        lifecycle_action=LifecycleAction.CONFIRM,
        target_signal_id=risk.signals[0].id,
        lifecycle_reason="仍然有效",
    )
    ingestion = SignalIngestion(
        risk.source,
        action,
        "confirm-reviewed",
        NOW_UTC,
        LifecycleAuthority("adapter", "task-8"),
    )
    snapshot = SignalValiditySnapshot(
        "weather_alert",
        "active",
        NOW_UTC,
        None,
        day_7,
        "until_revoked",
        "policy-confirm",
        {"code": "confirmed", "anchor_source": "published_at", "details": {}},
    )

    # When
    apply_lifecycle_action(db_session, ingestion, snapshot)

    # Then
    assert risk.signals[0].review_due_at == day_7
    assert risk.event.review_due_at == day_7
    assert risk.event.valid_until == day_7
    assert risk.alert.expires_at == day_7
    assert risk.alert.status == "current"

    revoke = SignalIngestion(
        risk.source,
        ManualSignalInput(
            external_id="revoke-reviewed",
            title="撤销",
            content="撤销",
            published_at=NOW_UTC,
            lifecycle_action=LifecycleAction.REVOKE,
            target_signal_id=risk.signals[0].id,
            lifecycle_reason="已撤销",
        ),
        "revoke-reviewed",
        NOW_UTC,
        LifecycleAuthority("adapter", "task-8"),
    )
    apply_lifecycle_action(db_session, revoke, snapshot)
    assert risk.signals[0].validity_state == "revoked"
    assert risk.event.validity_state == "expired"
    assert risk.alert.status == "expired"


def test_supersession_refreshes_old_event_and_alert_in_same_transaction(
    db_session: Session,
) -> None:
    # Given
    risk = linked_risk(
        db_session,
        (
            SignalSpec(
                "old-version",
                valid_until=NOW_UTC + timedelta(days=35),
                mode="until_superseded",
                profile="monthly_macro_indicator",
                validity_key="monthly-index",
            ),
        ),
        now_utc=NOW_UTC,
    )
    expire_alerts(db_session, now_utc=NOW_UTC)
    ingestion = SignalIngestion(
        risk.source,
        ManualSignalInput(
            external_id="new-version",
            title="新版本",
            content="新版本",
            published_at=NOW_UTC + timedelta(days=1),
            validity_profile=ValidityProfile.MONTHLY_MACRO_INDICATOR,
            validity_key="monthly-index",
        ),
        "new-version",
        NOW_UTC + timedelta(days=1),
        LifecycleAuthority("adapter", "task-8"),
    )

    # When
    persist_signal_ingestions(db_session, [ingestion])

    # Then
    assert risk.signals[0].validity_state == "superseded"
    assert risk.event.validity_state == "expired"
    assert risk.alert.status == "expired"
