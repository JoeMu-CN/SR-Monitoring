"""0063 迁移：情报门控的 skipped 状态与判定依据列。

锁定的行为：
- upgrade 后 status 约束接受 skipped；gate_reason / gate_prompt_version 可写。
- 非法状态仍被约束拒绝（约束确实是替换而非放开）。
- downgrade 删除 skipped 行并恢复旧约束：ready 行原样保留，两列消失，skipped 再次
  被拒绝——回退不会留下无法解释的状态。
"""

from __future__ import annotations

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
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from test_stack_guard import require_test_database_url

from app.config import DATABASE_URL

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(
    database_url: str, action: str, revision: str
) -> subprocess.CompletedProcess[str]:
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
    database_name = f"intelligence_gate_{uuid4().hex[:12]}_test"
    admin_url = base_url.set(drivername="postgresql", database="postgres")
    database_url = base_url.set(database=database_name).render_as_string(
        hide_password=False
    )

    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )
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
            connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )


@pytest.fixture
def migration_database() -> Generator[str]:
    with _isolated_database() as database_url:
        yield database_url


def _create_event(connection: Connection, suffix: str) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO risk_events (
                dedup_key, event_type, event_subtype, severity, summary,
                confidence, facts
            ) VALUES (
                :dedup_key, 'trade_policy', 'regulatory_change', 'medium',
                :summary, 0.8, '{}'::jsonb
            )
            RETURNING id
            """
        ),
        {
            "dedup_key": f"intelligence-gate-migration-{suffix}",
            "summary": f"事件摘要 {suffix}",
        },
    ).scalar_one()


def _create_report(
    connection: Connection,
    event_id: int,
    status: str,
    *,
    report_zh: str | None = None,
    gate_reason: str | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO intelligence_reports (
                event_id, category, report_zh, suggestions, provider, model,
                prompt_version, status, gate_reason, gate_prompt_version
            ) VALUES (
                :event_id, 'policy', :report_zh, '[]'::jsonb, 'fake',
                'fake-deterministic-v1', 'intelligence-report-v1', :status,
                :gate_reason, :gate_prompt_version
            )
            """
        ),
        {
            "event_id": event_id,
            "report_zh": report_zh,
            "status": status,
            "gate_reason": gate_reason,
            "gate_prompt_version": "intelligence-gate-v1"
            if status == "skipped"
            else None,
        },
    )


def _create_report_after_downgrade(
    connection: Connection, event_id: int, status: str
) -> None:
    """回退后已无 gate 列，用旧表结构写入，验证旧约束仍然拒绝 skipped。"""
    connection.execute(
        text(
            """
            INSERT INTO intelligence_reports (
                event_id, category, suggestions, provider, model,
                prompt_version, status
            ) VALUES (
                :event_id, 'policy', '[]'::jsonb, 'fake',
                'fake-deterministic-v1', 'intelligence-report-v1', :status
            )
            """
        ),
        {"event_id": event_id, "status": status},
    )


def test_upgrade_accepts_skipped_and_downgrade_cleans_it(
    migration_database: str,
) -> None:
    _run_alembic(migration_database, "upgrade", "0063")
    engine = create_engine(migration_database)
    try:
        with engine.begin() as connection:
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("intelligence_reports")
            }
            assert {"gate_reason", "gate_prompt_version"} <= columns
            skipped_event = _create_event(connection, "skipped")
            ready_event = _create_event(connection, "ready")
            _create_report(
                connection,
                skipped_event,
                "skipped",
                gate_reason="勘误类文书，未改变规则内容",
            )
            _create_report(connection, ready_event, "ready", report_zh="报告正文")

        # 约束是替换而非放开：未知状态仍被拒绝（独立事务，避免污染后续断言）
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _create_report(connection, _create_event(connection, "bad"), "other")

        _run_alembic(migration_database, "downgrade", "0062")

        with engine.begin() as connection:
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("intelligence_reports")
            }
            assert "gate_reason" not in columns
            assert "gate_prompt_version" not in columns
            statuses = connection.execute(
                text("SELECT status FROM intelligence_reports ORDER BY id")
            ).scalars().all()
            # skipped 行随回退删除；ready 行与正文原样保留
            assert statuses == ["ready"]
            assert (
                connection.execute(
                    text(
                        "SELECT report_zh FROM intelligence_reports "
                        "WHERE status = 'ready'"
                    )
                ).scalar_one()
                == "报告正文"
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _create_report_after_downgrade(
                    connection,
                    _create_event(connection, "after-downgrade"),
                    "skipped",
                )
    finally:
        engine.dispose()
