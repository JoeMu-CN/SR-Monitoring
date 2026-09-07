import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from test_stack_guard import require_test_database_url

from app.config import DATABASE_URL
from app.signals.schemas import DataSourceWrite
from app.signals.validity import ValidityMode

BACKEND_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_SIGNAL_SQL = text(
    """
    INSERT INTO raw_signals (
        source_id, title, content, fingerprint, raw_data,
        validity_profile, validity_state, valid_from, valid_until,
        validity_mode, validity_key, lifecycle_action,
        validity_policy_version, validity_reason
    ) VALUES (
        :source_id, :title, 'content', :fingerprint, '{}'::jsonb,
        'weather_alert', 'active', TIMESTAMPTZ '2026-09-01 00:00:00+00',
        :valid_until, :validity_mode, :validity_key, 'assert', 'policy-v1',
        '{"code":"policy_applied","anchor_source":"collected_at","details":{}}'::jsonb
    )
    """
)


def _run_alembic(database_url: str, action: str, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


@contextmanager
def _isolated_database() -> Generator[str]:
    require_test_database_url(DATABASE_URL)
    base_url = make_url(DATABASE_URL)
    database_name = f"signal_validity_{uuid4().hex[:12]}_test"
    admin_url = base_url.set(drivername="postgresql", database="postgres")
    database_url = base_url.set(database=database_name).render_as_string(hide_password=False)

    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        yield database_url
    finally:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


@pytest.fixture
def migration_database() -> Generator[str]:
    with _isolated_database() as database_url:
        yield database_url


def _seed_0046_history(connection: Connection) -> tuple[int, int, int]:
    source_id = connection.execute(
        text(
            """
            INSERT INTO data_sources (
                code, name, source_type, credibility, enabled, signal_validity_days
            ) VALUES ('migration-source', 'Migration Source', 'test', 80, true, 30)
            RETURNING id
            """
        )
    ).scalar_one()
    signal_id = connection.execute(
        text(
            """
            INSERT INTO raw_signals (
                source_id, title, content, fingerprint, raw_data
            ) VALUES (:source_id, 'legacy signal', 'content', 'legacy-fingerprint', '{}'::jsonb)
            RETURNING id
            """
        ),
        {"source_id": source_id},
    ).scalar_one()
    event_id = connection.execute(
        text(
            """
            INSERT INTO risk_events (
                dedup_key, event_type, severity, summary, confidence, facts
            ) VALUES ('legacy-event', 'weather', 'medium', 'legacy event', 0.8, '{}'::jsonb)
            RETURNING id
            """
        )
    ).scalar_one()
    supplier_id = connection.execute(
        text(
            """
            INSERT INTO suppliers (supplier_code, legal_name, country_code)
            VALUES ('MIGRATION-SUPPLIER', 'Migration Supplier', 'CN') RETURNING id
            """
        )
    ).scalar_one()
    match_id = connection.execute(
        text(
            """
            INSERT INTO supplier_event_matches (
                supplier_id, event_id, match_type, score, reasons, evidence
            ) VALUES (:supplier_id, :event_id, 'exact', 80, '[]'::jsonb, '[]'::jsonb)
            RETURNING id
            """
        ),
        {"supplier_id": supplier_id, "event_id": event_id},
    ).scalar_one()
    alert_id = connection.execute(
        text(
            """
            INSERT INTO risk_alerts (match_id, level, score, score_detail, status)
            VALUES (:match_id, 'P2', 80, '{}'::jsonb, 'current') RETURNING id
            """
        ),
        {"match_id": match_id},
    ).scalar_one()
    return signal_id, event_id, alert_id


def test_migration_preserves_legacy_rows_across_round_trip(
    migration_database: str,
) -> None:
    # Given: 0046 中已有信号、事件和提醒。
    _run_alembic(migration_database, "upgrade", "0046")
    isolated_engine = create_engine(migration_database)
    with isolated_engine.begin() as connection:
        signal_id, event_id, alert_id = _seed_0046_history(connection)

    # When: upgrade -> downgrade -> upgrade。
    _run_alembic(migration_database, "upgrade", "head")
    with isolated_engine.connect() as connection:
        signal_row = connection.execute(
            text(
                "SELECT validity_state, valid_until, validity_reason "
                "FROM raw_signals WHERE id = :id"
            ),
            {"id": signal_id},
        ).one()
        event_row = connection.execute(
            text(
                "SELECT validity_state, valid_until, validity_reason "
                "FROM risk_events WHERE id = :id"
            ),
            {"id": event_id},
        ).one()
        expiry_kind = connection.scalar(
            text("SELECT expiry_kind FROM risk_alerts WHERE id = :id"), {"id": alert_id}
        )
        policy = connection.scalar(
            text("SELECT validity_policy FROM data_sources WHERE code = 'migration-source'")
        )
    expected_reason = {
        "code": "legacy_unmigrated",
        "anchor_source": "legacy",
        "details": {},
    }
    assert tuple(signal_row) == ("legacy", None, expected_reason)
    assert tuple(event_row) == ("legacy", None, expected_reason)
    assert expiry_kind == "legacy"
    assert policy == {"mode": "fixed_days", "fixed_days": 30}

    _run_alembic(migration_database, "downgrade", "0046")
    with isolated_engine.connect() as connection:
        assert "validity_state" not in {
            column["name"] for column in inspect(connection).get_columns("raw_signals")
        }
        assert "signal_validity_days" in {
            column["name"] for column in inspect(connection).get_columns("data_sources")
        }
    _run_alembic(migration_database, "upgrade", "head")

    # Then: 再次升级仍将旧行精确标为 legacy。
    with isolated_engine.connect() as connection:
        assert connection.scalar(
            text("SELECT validity_state FROM raw_signals WHERE id = :id"), {"id": signal_id}
        ) == "legacy"
        assert connection.scalar(
            text("SELECT expiry_kind FROM risk_alerts WHERE id = :id"), {"id": alert_id}
        ) == "legacy"
    isolated_engine.dispose()


def test_database_constraints_reject_invalid_validity_combinations(
    migration_database: str,
) -> None:
    # Given
    _run_alembic(migration_database, "upgrade", "head")
    isolated_engine = create_engine(migration_database)
    with isolated_engine.begin() as connection:
        source_id = connection.execute(
            text(
                "INSERT INTO data_sources (code, name, source_type, credibility, enabled) "
                "VALUES ('constraint-source', 'Constraint Source', 'test', 80, true) RETURNING id"
            )
        ).scalar_one()

    # When / Then: mode、截止时间、key 与策略天数均由 PostgreSQL 拒绝非法组合。
    invalid_rows = (
        ("missing-key", "until_superseded", None, "2026-09-02T00:00:00+00:00"),
        ("missing-deadline", "fixed_days", None, None),
        ("missing-mode", None, None, None),
    )
    for title, mode, key, deadline in invalid_rows:
        with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
            connection.execute(
                ACTIVE_SIGNAL_SQL,
                {
                    "source_id": source_id,
                    "title": title,
                    "fingerprint": title,
                    "validity_mode": mode,
                    "validity_key": key,
                    "valid_until": deadline,
                },
            )

    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_sources SET validity_policy = CAST(:policy AS jsonb) "
                "WHERE id = :source_id"
            ),
            {
                "policy": '{"mode":"fixed_days","fixed_days":3651}',
                "source_id": source_id,
            },
        )

    with isolated_engine.begin() as connection:
        connection.execute(
            ACTIVE_SIGNAL_SQL,
            {
                "source_id": source_id,
                "title": "first",
                "fingerprint": "first",
                "validity_mode": "until_superseded",
                "validity_key": "monthly-key",
                "valid_until": "2026-09-02T00:00:00+00:00",
            },
        )
        connection.execute(
            ACTIVE_SIGNAL_SQL,
            {
                "source_id": source_id,
                "title": "explicit-unbounded",
                "fingerprint": "explicit-unbounded",
                "validity_mode": "indefinite",
                "validity_key": None,
                "valid_until": None,
            },
        )
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            ACTIVE_SIGNAL_SQL,
            {
                "source_id": source_id,
                "title": "second",
                "fingerprint": "second",
                "validity_mode": "until_superseded",
                "validity_key": "monthly-key",
                "valid_until": "2026-09-03T00:00:00+00:00",
            },
        )
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE raw_signals SET validity_reason = "
                "CAST(:reason AS jsonb) WHERE fingerprint = 'first'"
            ),
            {
                "reason": (
                    '{"code":"invalid","anchor_source":"collected_at",'
                    '"details":"must-be-object"}'
                )
            },
        )
    isolated_engine.dispose()


def test_downgrade_refuses_to_drop_expired_suppressed_audit(
    migration_database: str,
) -> None:
    # Given
    _run_alembic(migration_database, "upgrade", "head")
    isolated_engine = create_engine(migration_database)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO notification_deliveries (channel, status) "
                "VALUES ('test', 'expired_suppressed')"
            )
        )

    # When
    with pytest.raises(subprocess.CalledProcessError) as caught:
        _run_alembic(migration_database, "downgrade", "0046")

    # Then
    assert "expired_suppressed" in caught.value.stderr
    isolated_engine.dispose()


def test_legacy_days_normalize_and_conflicts_are_rejected() -> None:
    # Given / When
    payload = DataSourceWrite(
        code="legacy-source",
        name="Legacy Source",
        source_type="test",
        credibility=80,
        signal_validity_days=3,
    )

    # Then
    assert payload.validity_policy is not None
    assert payload.validity_policy.mode is ValidityMode.FIXED_DAYS
    assert payload.validity_policy.fixed_days == 3
    with pytest.raises(ValidationError):
        DataSourceWrite(
            code="conflict-source",
            name="Conflict Source",
            source_type="test",
            credibility=80,
            signal_validity_days=3,
            validity_policy={"mode": "fixed_days", "fixed_days": 30},
        )
