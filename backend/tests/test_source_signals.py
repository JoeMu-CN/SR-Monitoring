"""信息源采集记录只读清单和有效期策略契约测试。"""

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.signals.models import DataSource, DataSourceAuditLog, RawSignal


def _source(session: Session, code: str, validity_days: int | None) -> DataSource:
    source = DataSource(
        code=code,
        name=f"测试信息源 {code}",
        source_type="official_api",
        credibility=90,
        auth_type="none",
        login_config={},
        adapter_config={},
        adapter_status="builtin",
        adapter_version=0,
        enabled=True,
        signal_validity_days=validity_days,
    )
    session.add(source)
    session.flush()
    return source


def _signal(
    session: Session,
    source: DataSource,
    index: int,
    *,
    published_at: datetime | None,
    collected_at: datetime,
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=f"external-{index}",
        title=f"采集记录 {index}",
        content=f"采集记录正文 {index}",
        url=f"https://example.test/signals/{index}",
        published_at=published_at,
        collected_at=collected_at,
        fingerprint=f"fingerprint-{source.code}-{index}",
        raw_data={"secret": index},
    )
    session.add(signal)
    session.flush()
    return signal


def test_validity_policy_version_is_order_independent_and_applies_to_new_signals_only(
    client: TestClient,
    db_session: Session,
    auth_as,
) -> None:
    auth_as("risk_admin", "validity-policy-admin")
    first_payload = {
        "code": "versioned-policy-first",
        "name": "策略版本首个信源",
        "source_type": "official_api",
        "credibility": 90,
        "enabled": False,
        "validity_policy": {"mode": "fixed_days", "fixed_days": 3},
    }
    second_payload = {
        "code": "versioned-policy-second",
        "name": "策略版本次个信源",
        "source_type": "official_api",
        "credibility": 90,
        "enabled": False,
        "validity_policy": {"fixed_days": 3, "mode": "fixed_days"},
    }

    first_created = client.post("/api/v1/sources", json=first_payload)
    second_created = client.post("/api/v1/sources", json=second_payload)

    assert first_created.status_code == second_created.status_code == 201
    first_body = first_created.json()
    second_body = second_created.json()
    assert first_body["applies_to"] == second_body["applies_to"] == "new_signals_only"
    assert first_body["validity_policy_version"] == second_body["validity_policy_version"]
    assert first_body["validity_policy"]["version"] == first_body["validity_policy_version"]

    source = db_session.get(DataSource, first_body["id"])
    assert source is not None
    existing_signal = _signal(
        db_session,
        source,
        1,
        published_at=datetime.now(UTC),
        collected_at=datetime.now(UTC),
    )
    raw_snapshot = (
        existing_signal.validity_profile,
        existing_signal.validity_state,
        existing_signal.valid_from,
        existing_signal.valid_until,
        existing_signal.review_due_at,
        existing_signal.validity_mode,
        existing_signal.validity_policy_version,
    )
    db_session.commit()

    updated = client.put(
        f"/api/v1/sources/{source.id}",
        json={
            "validity_policy": {
                "mode": "until_revoked",
            }
        },
    )

    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["applies_to"] == "new_signals_only"
    assert updated_body["validity_policy_version"] != first_body["validity_policy_version"]
    db_session.refresh(existing_signal)
    assert (
        existing_signal.validity_profile,
        existing_signal.validity_state,
        existing_signal.valid_from,
        existing_signal.valid_until,
        existing_signal.review_due_at,
        existing_signal.validity_mode,
        existing_signal.validity_policy_version,
    ) == raw_snapshot

    audit_logs = list(
        db_session.scalars(
            select(DataSourceAuditLog)
            .where(DataSourceAuditLog.source_id == source.id)
            .order_by(DataSourceAuditLog.id)
        )
    )
    assert [item.action for item in audit_logs] == ["created", "updated"]
    assert audit_logs[-1].changes["validity_policy_version"] == updated_body[
        "validity_policy_version"
    ]
    assert all("secret" not in json.dumps(item.changes) for item in audit_logs)


def test_validity_policy_conflict_returns_422_without_source_or_audit_write(
    client: TestClient,
    db_session: Session,
    auth_as,
) -> None:
    auth_as("risk_admin", "validity-policy-conflict-admin")
    source_count = db_session.scalar(select(func.count()).select_from(DataSource))
    audit_count = db_session.scalar(select(func.count()).select_from(DataSourceAuditLog))

    response = client.post(
        "/api/v1/sources",
        json={
            "code": "conflicting-validity-policy",
            "name": "冲突有效期策略",
            "source_type": "official_api",
            "credibility": 90,
            "enabled": False,
            "signal_validity_days": 3,
            "validity_policy": {"mode": "fixed_days", "fixed_days": 30},
        },
    )

    assert response.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(DataSource)) == source_count
    assert (
        db_session.scalar(select(func.count()).select_from(DataSourceAuditLog)) == audit_count
    )
