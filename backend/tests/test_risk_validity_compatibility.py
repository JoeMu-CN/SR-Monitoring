"""风险有效期策略、规则版本与 legacy 兼容切割测试。"""

from datetime import UTC, datetime, timedelta

from risk_validity_fixtures import SignalSpec, linked_risk
from sqlalchemy.orm import Session

from app.risks.engine.alert_persistence import AlertValues, upsert_alert
from app.risks.models import SupplierEventMatch
from app.risks.scoring import ScoringSettings
from app.risks.validity import compute_alert_expires_at, expire_alerts

NOW_UTC = datetime(2026, 9, 6, 12, tzinfo=UTC)


def test_legacy_alert_and_event_are_not_rewritten_by_new_configuration(
    db_session: Session,
) -> None:
    # Given
    legacy_deadline = NOW_UTC + timedelta(days=30)
    risk = linked_risk(
        db_session,
        (SignalSpec("legacy-cut", valid_until=NOW_UTC + timedelta(days=3)),),
        now_utc=NOW_UTC,
    )
    risk.source.validity_policy = {
        "mode": "fixed_days",
        "fixed_days": 90,
    }
    risk.event.validity_state = "legacy"
    risk.event.valid_until = legacy_deadline
    risk.event.review_due_at = None
    risk.event.validity_policy_version = None
    risk.event.validity_reason = {
        "code": "legacy_unmigrated",
        "anchor_source": "legacy",
        "details": {},
    }
    risk.alert.expiry_kind = "legacy"
    risk.alert.expires_at = legacy_deadline

    # When
    expired = expire_alerts(db_session, now_utc=NOW_UTC)

    # Then
    assert expired == 0
    assert risk.event.validity_state == "legacy"
    assert risk.event.valid_until == legacy_deadline
    assert risk.event.validity_policy_version is None
    assert risk.alert.status == "current"
    assert risk.alert.expiry_kind == "legacy"
    assert risk.alert.expires_at == legacy_deadline


def test_policy_and_rule_changes_do_not_recompute_stored_deadline(
    db_session: Session,
) -> None:
    # Given
    stored_deadline = NOW_UTC + timedelta(days=3)
    risk = linked_risk(
        db_session,
        (SignalSpec("configuration-cut", valid_until=stored_deadline),),
        now_utc=NOW_UTC,
    )
    expire_alerts(db_session, now_utc=NOW_UTC)
    stored_version = risk.event.validity_policy_version

    # When
    risk.source.validity_policy = {
        "mode": "fixed_days",
        "fixed_days": 90,
    }
    projected = compute_alert_expires_at(
        risk.event, ScoringSettings(alert_expiry_days=365)
    )
    expire_alerts(db_session, now_utc=NOW_UTC + timedelta(days=1))

    # Then
    assert projected == stored_deadline
    assert risk.signals[0].valid_until == stored_deadline
    assert risk.event.valid_until == stored_deadline
    assert risk.event.validity_policy_version == stored_version
    assert risk.alert.expires_at == stored_deadline


def test_new_rule_version_does_not_rewrite_current_legacy_alert(
    db_session: Session,
) -> None:
    # Given
    legacy_deadline = NOW_UTC + timedelta(days=30)
    risk = linked_risk(
        db_session,
        (SignalSpec("legacy-rule", valid_until=legacy_deadline),),
        now_utc=NOW_UTC,
    )
    risk.alert.expiry_kind = "legacy"
    risk.alert.expires_at = legacy_deadline
    match = db_session.get(SupplierEventMatch, risk.alert.match_id)
    assert match is not None

    # When
    alert = upsert_alert(
        db_session,
        match,
        AlertValues(
            level="P1",
            score=100,
            score_detail={"rule_version": "rule-v2"},
            expires_at=NOW_UTC + timedelta(days=365),
            now_utc=NOW_UTC,
        ),
    )

    # Then
    assert alert.id == risk.alert.id
    assert alert.level == "P3"
    assert alert.score_detail["rule_version"] == "rule-v1"
    assert alert.expiry_kind == "legacy"
    assert alert.expires_at == legacy_deadline
