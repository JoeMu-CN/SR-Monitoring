"""任务 12 风险基线重建核心的隔离数据库测试。

锁定两部分契约：
1. 核心库函数 ``reset_risk_baseline(ResetRiskBaselineRequest)`` 的全部门禁与单事务语义；
2. 与 ``scripts/reset-risk-baseline.ps1`` 适配的 CLI 契约
   （``python -m app.maintenance.reset_risk_baseline preflight|execute``），
   execute 必须接受 ``--expected-plan-sha256``、``--backup-path``、``--restore-receipt-path``、
   ``--active-writer-count``，并重新验证备份/恢复收据/写进程/指纹/计划摘要。

所有数据库操作只针对隔离库 ``supplier_risk_reset_test``，绝不触碰白名单业务库之外的目标。
"""

import json
import os
import subprocess
import sys
from collections.abc import Generator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_stack_guard import ALLOWED_TEST_DATABASE_NAMES

from app.database import Base
from app.database import engine as test_engine
from app.maintenance.reset_risk_baseline import (
    DELETE_ORDER,
    EXPECTED_FOREIGN_KEYS,
    BackupReceipt,
    ResetGateError,
    ResetRiskBaselineRequest,
    reset_risk_baseline,
)

TARGET_DATABASE = "supplier_risk_reset_test"
MODULE = "app.maintenance.reset_risk_baseline"
MIGRATION_VERSION = "0048_source_membership_state"
RESTORE_RECEIPT_SCHEMA = "supplier-risk-monitoring/risk-baseline-restore-receipt/v1"
PREFLIGHT_SCHEMA = "supplier-risk-monitoring/risk-baseline-preflight/v1"
EXECUTION_RECEIPT_SCHEMA = "supplier-risk-monitoring/risk-baseline-execution-receipt/v1"
FK_ALLOWLIST_POLICY = "supplier-risk-monitoring/risk-baseline-fk-allowlist/v1"
CONFIRM_PHRASE = f"RESET RISK BASELINE {TARGET_DATABASE}"


@dataclass(frozen=True, slots=True)
class ResetDatabase:
    engine: Engine
    url: str
    backup_path: Path
    backup_sha256: str
    receipt_path: Path


@pytest.fixture
def reset_database(tmp_path: Path) -> Generator[ResetDatabase]:
    # Given: pytest 只能从既有白名单测试库创建专用隔离库；目标库名必须显式。
    assert test_engine.url.database == "supplier_risk_test"
    test_url = make_url(test_engine.url.render_as_string(hide_password=False))
    admin_url = test_url.set(database="postgres")
    target_url = test_url.set(database=TARGET_DATABASE)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{TARGET_DATABASE}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{TARGET_DATABASE}"'))
    target_engine = create_engine(target_url)
    with target_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        Base.metadata.create_all(connection)
        connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        connection.execute(
            text(f"INSERT INTO alembic_version VALUES ('{MIGRATION_VERSION}')")
        )
    backup_path = tmp_path / "isolated.backup"
    backup_path.write_bytes(b"isolated-postgresql-backup")
    backup_hash = sha256(backup_path.read_bytes()).hexdigest()
    receipt_path = tmp_path / "restore-receipt.json"

    try:
        yield ResetDatabase(
            target_engine,
            target_url.render_as_string(hide_password=False),
            backup_path,
            backup_hash,
            receipt_path,
        )
    finally:
        target_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{TARGET_DATABASE}" WITH (FORCE)'))
        admin_engine.dispose()


def write_restore_receipt(
    path: Path,
    backup_sha256: str,
    backup_size: int,
    *,
    source_database: str,
    source_fingerprint: str,
    restored_database: str = "supplier_risk_restore_test",
    migration_version: str = MIGRATION_VERSION,
) -> None:
    """写入一份 PowerShell 契约规定的恢复演练收据 JSON。"""
    counts = {
        "raw_signals": 1,
        "ai_analysis_records": 1,
        "risk_events": 1,
        "risk_alerts": 1,
        "notification_deliveries": 2,
        "notification_runtime_state": 0,
        "collection_runs": 1,
        "source_member_states": 1,
        "research_claims": 1,
        "research_claims_promoted_nonnull": 1,
    }
    receipt = {
        "schema": RESTORE_RECEIPT_SCHEMA,
        "status": "passed",
        "backup": {"sha256": backup_sha256, "size_bytes": backup_size},
        "source_target": {
            "environment": "test",
            "database_name": source_database,
            "fingerprint": source_fingerprint,
            "migration_version": migration_version,
            "critical_table_counts": counts,
        },
        "restored_target": {
            "environment": "test",
            "database_name": restored_database,
            "fingerprint": "f" * 64,
            "migration_version": migration_version,
            "critical_table_counts": counts,
        },
    }
    path.write_text(json.dumps(receipt, separators=(",", ":")), encoding="utf-8")


def request_for(
    database: ResetDatabase,
    *,
    dry_run: bool = True,
    active_writer_count: int = 0,
    backup_sha256: str | None = None,
    approved_plan_hash: str | None = None,
) -> ResetRiskBaselineRequest:
    actual_backup_hash = backup_sha256 or database.backup_sha256
    return ResetRiskBaselineRequest(
        database_url=SecretStr(database.url),
        target_database_name=TARGET_DATABASE,
        environment="test",
        dry_run=dry_run,
        confirmation_phrase=("" if dry_run else CONFIRM_PHRASE),
        backup_path=database.backup_path,
        backup_sha256=actual_backup_hash,
        restore_receipt=BackupReceipt(
            restored=True,
            backup_sha256=database.backup_sha256,
            restored_database_name="supplier_risk_restore_test",
        ),
        active_writer_count=active_writer_count,
        approved_foreign_keys=EXPECTED_FOREIGN_KEYS,
        approved_plan_hash=approved_plan_hash,
    )


def seed_reset_graph(engine: Engine) -> tuple[int, int]:
    with engine.begin() as connection:
        source_id = connection.scalar(text("""
            INSERT INTO data_sources (code,name,source_type,credibility)
            VALUES ('reset-source','Reset','api',90) RETURNING id
        """))
        supplier_id = connection.scalar(text("""
            INSERT INTO suppliers (supplier_code,legal_name,country_code)
            VALUES ('RESET-1','Reset Supplier','CN') RETURNING id
        """))
        user_id = connection.scalar(text("""
            INSERT INTO users (username,password_hash,display_name,role,status)
            VALUES ('reset-user','x','Reset User','platform_admin','active') RETURNING id
        """))
        signal_id = connection.scalar(
            text("""
                INSERT INTO raw_signals (source_id,title,content,fingerprint,raw_data)
                VALUES (:source_id,'signal','signal','reset-signal','{}') RETURNING id
            """),
            {"source_id": source_id},
        )
        event_id = connection.scalar(text("""
            INSERT INTO risk_events
                (dedup_key,event_type,severity,summary,confidence,facts)
            VALUES ('reset-event','weather','high','event',0.9,'{}') RETURNING id
        """))
        connection.execute(
            text("INSERT INTO risk_event_signals VALUES (:event_id,:signal_id)"),
            {"event_id": event_id, "signal_id": signal_id},
        )
        connection.execute(
            text("""
                INSERT INTO event_entities (event_id,name,normalized_name)
                VALUES (:event_id,'Entity','entity')
            """),
            {"event_id": event_id},
        )
        connection.execute(
            text("""
                INSERT INTO event_locations (event_id,name,normalized_name)
                VALUES (:event_id,'Place','place')
            """),
            {"event_id": event_id},
        )
        match_id = connection.scalar(
            text("""
                INSERT INTO supplier_event_matches
                    (supplier_id,event_id,match_type,score,reasons,evidence)
                VALUES (:supplier_id,:event_id,'legal_name',30,'[]','[]') RETURNING id
            """),
            {"supplier_id": supplier_id, "event_id": event_id},
        )
        alert_id = connection.scalar(
            text("""
                INSERT INTO risk_alerts (match_id,level,score,score_detail,status)
                VALUES (:match_id,'P3',60,'{}','current') RETURNING id
            """),
            {"match_id": match_id},
        )
        connection.execute(
            text("""
                INSERT INTO notification_deliveries (alert_id,channel,status)
                VALUES (:alert_id,'email','success'), (NULL,'email','merged')
            """),
            {"alert_id": alert_id},
        )
        connection.execute(
            text("INSERT INTO notification_subscriptions (channel) VALUES ('email')")
        )
        connection.execute(
            text("""
                INSERT INTO ai_analysis_records
                    (signal_id,provider,model,prompt_version,status)
                VALUES (:signal_id,'fake','fake','v1','succeeded')
            """),
            {"signal_id": signal_id},
        )
        connection.execute(
            text("INSERT INTO collection_runs (source_id,status) VALUES (:source_id,'succeeded')"),
            {"source_id": source_id},
        )
        connection.execute(
            text("""
                INSERT INTO source_member_states
                    (source_id,member_key,first_seen_at,last_seen_at,baseline_snapshot_hash)
                VALUES (:source_id,'member-1',now(),now(),'baseline')
            """),
            {"source_id": source_id},
        )
        task_id = connection.scalar(
            text("""
                INSERT INTO research_tasks (owner_user_id,topic,status)
                VALUES (:user_id,'reset provenance','succeeded') RETURNING id
            """),
            {"user_id": user_id},
        )
        claim_id = connection.scalar(
            text("""
                INSERT INTO research_claims
                    (task_id,claim_type,text,promoted_signal_id)
                VALUES (:task_id,'fact','claim',:signal_id) RETURNING id
            """),
            {"task_id": task_id, "signal_id": signal_id},
        )
    assert isinstance(claim_id, int)
    assert isinstance(signal_id, int)
    return claim_id, signal_id


def seed_and_preflight(database: ResetDatabase) -> tuple[str, str]:
    """种子数据并执行 CLI read-only 盘点，返回 (fingerprint, plan_sha256)。

    必须走 CLI preflight，因为 CLI execute 用 ``target_snapshot`` 计算指纹/计划摘要，
    与库函数 ``reset_risk_baseline`` 的算法不同。
    """
    seed_reset_graph(database.engine)
    completed = run_cli(
        database,
        "preflight",
        "--environment",
        "test",
        "--database-name",
        TARGET_DATABASE,
        "--output",
        "json",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    return str(payload["target"]["fingerprint"]), str(payload["plan_sha256"])


def run_cli(database: ResetDatabase, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database.url
    return subprocess.run(
        [sys.executable, "-m", MODULE, *arguments],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def run_cli_error(database: ResetDatabase, *arguments: str) -> str:
    completed = run_cli(database, *arguments)
    assert completed.returncode == 2, (completed.stdout, completed.stderr)
    assert completed.stdout == ""
    return str(json.loads(completed.stderr)["error"])


# ---------------------------------------------------------------- 基线表征测试

def test_existing_test_database_gate_is_explicit() -> None:
    # Given
    expected_names = frozenset({"supplier_risk_test"})

    # When
    actual_names = ALLOWED_TEST_DATABASE_NAMES

    # Then
    assert actual_names == expected_names


def test_retention_delete_order_documents_fk_safe_semantics() -> None:
    # Given
    retention_source = (
        Path(__file__).parents[1] / "app" / "scheduler" / "retention.py"
    ).read_text(encoding="utf-8")

    # When
    alert_delete = retention_source.index("session.delete(alert)")
    match_delete = retention_source.index("session.delete(match)")
    event_delete = retention_source.index("session.delete(event)")

    # Then
    assert alert_delete < match_delete < event_delete


def test_catalog_has_exact_allowed_cross_domain_set_null_foreign_keys(
    db_session: Session,
) -> None:
    # Given
    expected = {
        ("notification_deliveries", "alert_id", "risk_alerts", "id", "SET NULL"),
        ("research_claims", "promoted_signal_id", "raw_signals", "id", "SET NULL"),
    }

    # When
    rows = db_session.execute(
        text(
            """
            SELECT child.relname, child_col.attname, parent.relname,
                   parent_col.attname,
                   CASE fk.confdeltype WHEN 'n' THEN 'SET NULL' ELSE fk.confdeltype::text END
            FROM pg_constraint AS fk
            JOIN pg_class AS child ON child.oid = fk.conrelid
            JOIN pg_class AS parent ON parent.oid = fk.confrelid
            JOIN pg_attribute AS child_col
              ON child_col.attrelid = child.oid AND child_col.attnum = fk.conkey[1]
            JOIN pg_attribute AS parent_col
              ON parent_col.attrelid = parent.oid AND parent_col.attnum = fk.confkey[1]
            WHERE fk.contype = 'f'
              AND fk.confdeltype = 'n'
              AND parent.relname IN ('raw_signals', 'risk_alerts')
            ORDER BY child.relname, child_col.attname
            """
        )
    ).tuples()

    # Then
    assert set(rows) == expected


# ---------------------------------------------------------------- 核心库函数门禁

@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"backup_path": Path("missing.backup")}, "backup_missing"),
        ({"backup_sha256": "0" * 64}, "backup_hash_mismatch"),
        ({"active_writer_count": 1}, "active_writers"),
        ({"target_database_name": "supplier_risk"}, "database_mismatch"),
    ],
)
def test_execution_is_blocked_when_external_gate_fails(
    reset_database: ResetDatabase,
    change: dict[str, object],
    expected_code: str,
) -> None:
    # Given
    request = request_for(reset_database).model_copy(update=change)

    # When
    with pytest.raises(ResetGateError) as caught:
        reset_risk_baseline(request)

    # Then
    assert caught.value.code == expected_code


def test_execution_is_blocked_when_restore_receipt_hash_differs(
    reset_database: ResetDatabase,
) -> None:
    # Given
    request = request_for(reset_database).model_copy(
        update={
            "restore_receipt": BackupReceipt(
                restored=True,
                backup_sha256="0" * 64,
                restored_database_name="supplier_risk_restore_test",
            )
        }
    )

    # When
    with pytest.raises(ResetGateError) as caught:
        reset_risk_baseline(request)

    # Then
    assert caught.value.code == "restore_receipt_mismatch"


# ---------------------------------------------------------------- 核心库函数盘点/删除语义

def test_dry_run_reports_inventory_without_deleting(reset_database: ResetDatabase) -> None:
    # Given
    claim_id, signal_id = seed_reset_graph(reset_database.engine)

    # When
    result = reset_risk_baseline(request_for(reset_database))

    # Then
    assert result.dry_run is True
    assert result.claim_count == 1
    assert result.promoted_claim_count == 1
    assert result.claim_provenance == ((claim_id, signal_id),)
    expected_manifest = json.dumps(
        [{"id": claim_id, "promoted_signal_id": signal_id}],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert result.claim_provenance_sha256 == sha256(expected_manifest).hexdigest()
    assert dict(result.planned_counts)["notification_deliveries"] == 2
    assert all(count == 0 for _, count in result.deleted_counts)
    with reset_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM raw_signals")) == 1


def test_unknown_foreign_key_blocks_reset(reset_database: ResetDatabase) -> None:
    # Given
    seed_reset_graph(reset_database.engine)
    with reset_database.engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE unknown_risk_reference (
                id bigint PRIMARY KEY,
                signal_id bigint REFERENCES raw_signals(id)
            )
        """))

    # When
    with pytest.raises(ResetGateError) as caught:
        reset_risk_baseline(request_for(reset_database))

    # Then
    assert caught.value.code == "foreign_key_drift"


def test_catalog_order_prevents_set_null_from_hiding_deliveries(
    reset_database: ResetDatabase,
) -> None:
    # Given
    seed_reset_graph(reset_database.engine)

    # When: 错误顺序会静默置空，固定核心必须先删 delivery。
    with reset_database.engine.connect() as connection, connection.begin() as transaction:
        connection.execute(text("DELETE FROM risk_alerts"))
        hidden_count = connection.scalar(
            text("SELECT count(*) FROM notification_deliveries WHERE alert_id IS NULL")
        )
        transaction.rollback()

    # Then
    assert hidden_count == 2
    assert DELETE_ORDER.index("notification_deliveries") < DELETE_ORDER.index("risk_alerts")


def test_transaction_failure_rolls_back_every_delete(reset_database: ResetDatabase) -> None:
    # Given
    seed_reset_graph(reset_database.engine)
    dry_run = reset_risk_baseline(request_for(reset_database))
    request = request_for(
        reset_database,
        dry_run=False,
        approved_plan_hash=dry_run.plan_sha256,
    ).model_copy(update={"fail_after_table": "risk_alerts"})

    # When
    with pytest.raises(ResetGateError) as caught:
        reset_risk_baseline(request)

    # Then
    assert caught.value.code == "injected_failure"
    with reset_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM notification_deliveries")) == 2
        assert connection.scalar(text("SELECT count(*) FROM risk_alerts")) == 1


def test_execute_preserves_domains_nulls_claim_link_and_keeps_identity(
    reset_database: ResetDatabase,
) -> None:
    # Given
    claim_id, signal_id = seed_reset_graph(reset_database.engine)
    dry_run = reset_risk_baseline(request_for(reset_database))

    # When
    result = reset_risk_baseline(
        request_for(reset_database, dry_run=False, approved_plan_hash=dry_run.plan_sha256)
    )

    # Then
    assert result.dry_run is False
    assert dict(result.deleted_counts)["notification_deliveries"] == 2
    assert result.promoted_claim_count == 0
    assert result.claim_provenance == ((claim_id, signal_id),)
    assert result.database_name == TARGET_DATABASE
    assert result.alembic_version == MIGRATION_VERSION
    preserved = dict(result.preserved_counts)
    assert preserved["notification_subscriptions"] == 1
    assert preserved["suppliers"] == 1
    assert preserved["data_sources"] == 1
    assert preserved["research_claims"] == 1
    assert preserved["users"] == 1
    with reset_database.engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM raw_signals")) == 0
        assert connection.scalar(text("SELECT count(*) FROM research_claims")) == 1
        promoted_count = connection.scalar(
            text("SELECT count(*) FROM research_claims WHERE promoted_signal_id IS NOT NULL")
        )
        next_signal_id = connection.scalar(text("""
            INSERT INTO raw_signals (source_id,title,content,fingerprint,raw_data)
            SELECT id,'next','next','next-signal','{}'
            FROM data_sources WHERE code='reset-source' RETURNING id
        """))
    assert promoted_count == 0
    assert isinstance(next_signal_id, int)
    assert next_signal_id > signal_id


def test_second_execution_is_idempotent(reset_database: ResetDatabase) -> None:
    # Given
    seed_reset_graph(reset_database.engine)
    first_dry_run = reset_risk_baseline(request_for(reset_database))
    reset_risk_baseline(
        request_for(
            reset_database,
            dry_run=False,
            approved_plan_hash=first_dry_run.plan_sha256,
        )
    )
    second_dry_run = reset_risk_baseline(request_for(reset_database))

    # When
    second_result = reset_risk_baseline(
        request_for(reset_database, dry_run=False, approved_plan_hash=second_dry_run.plan_sha256)
    )

    # Then
    assert all(count == 0 for _, count in second_result.deleted_counts)
    assert second_result.claim_count == 1
    assert second_result.promoted_claim_count == 0


# ---------------------------------------------------------------- CLI preflight 契约

def test_preflight_cli_emits_read_only_machine_contract(
    reset_database: ResetDatabase,
) -> None:
    # Given
    seed_reset_graph(reset_database.engine)

    # When
    completed = run_cli(
        reset_database,
        "preflight",
        "--environment",
        "test",
        "--database-name",
        TARGET_DATABASE,
        "--output",
        "json",
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == PREFLIGHT_SCHEMA
    assert payload["status"] == "passed"
    assert payload["mode"] == "preflight"
    assert payload["target"]["database_name"] == TARGET_DATABASE
    assert payload["target"]["critical_table_counts"]["raw_signals"] == 1
    assert payload["target"]["critical_table_counts"]["notification_deliveries"] == 2
    assert len(payload["target"]["fingerprint"]) == 64
    assert len(payload["target"]["content_fingerprint"]) == 64
    assert len(payload["plan_sha256"]) == 64
    assert payload["fk_inventory"]["policy"] == FK_ALLOWLIST_POLICY
    assert payload["fk_inventory"]["status"] == "passed"
    assert payload["fk_inventory"]["missing_edges"] == []
    assert payload["fk_inventory"]["unexpected_edges"] == []
    assert len(payload["fk_inventory"]["observed_edges"]) == len(EXPECTED_FOREIGN_KEYS)
    with reset_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM raw_signals")) == 1


def test_cli_blocks_unsafe_target_without_disclosing_database_url(
    reset_database: ResetDatabase,
) -> None:
    # Given
    secret_fragment = make_url(reset_database.url).password
    assert isinstance(secret_fragment, str)

    # When
    completed = run_cli(
        reset_database,
        "preflight",
        "--environment",
        "production",
        "--database-name",
        TARGET_DATABASE,
        "--output",
        "json",
    )

    # Then
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr)["error"] == "environment_mismatch"
    assert secret_fragment not in completed.stderr


# ---------- CLI execute 契约（PowerShell 适配） ----------

def _prepare_execute(database: ResetDatabase) -> tuple[str, str]:
    """种子 + preflight + 写合法收据；返回 (fingerprint, plan_sha256)。"""
    fingerprint, plan_sha256 = seed_and_preflight(database)
    write_restore_receipt(
        database.receipt_path,
        database.backup_sha256,
        database.backup_path.stat().st_size,
        source_database=TARGET_DATABASE,
        source_fingerprint=fingerprint,
    )
    return fingerprint, plan_sha256


def _execute_args(
    fingerprint: str,
    plan_sha256: str,
    backup_path: Path,
    receipt_path: Path,
    *,
    expected_fingerprint: str | None = None,
    expected_plan_sha256: str | None = None,
    confirm_phrase: str = CONFIRM_PHRASE,
    active_writer_count: int = 0,
) -> list[str]:
    return [
        "execute",
        "--environment",
        "test",
        "--database-name",
        TARGET_DATABASE,
        "--output",
        "json",
        "--expected-fingerprint",
        expected_fingerprint if expected_fingerprint is not None else fingerprint,
        "--expected-plan-sha256",
        expected_plan_sha256 if expected_plan_sha256 is not None else plan_sha256,
        "--confirm-phrase",
        confirm_phrase,
        "--backup-path",
        str(backup_path),
        "--restore-receipt-path",
        str(receipt_path),
        "--active-writer-count",
        str(active_writer_count),
    ]


def test_execute_cli_accepts_powershell_contract_and_emits_receipt(
    reset_database: ResetDatabase,
) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)

    # When
    completed = run_cli(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
        ),
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == EXECUTION_RECEIPT_SCHEMA
    assert payload["status"] == "completed"
    assert payload["dry_run"] is False
    assert payload["environment"] == "test"
    assert payload["database_name"] == TARGET_DATABASE
    assert payload["pre_reset_fingerprint"] == fingerprint
    assert payload["pre_reset_plan_sha256"] == plan_sha256
    assert dict(payload["deleted_counts"])["notification_deliveries"] == 2
    assert dict(payload["deleted_counts"])["raw_signals"] == 1
    preserved = dict(payload["preserved_counts"])
    assert preserved["research_claims"] == 1
    assert preserved["data_sources"] == 1
    assert preserved["suppliers"] == 1
    assert payload["claim_count"] == 1
    assert payload["promoted_claim_count"] == 0
    assert len(payload["claim_provenance_sha256"]) == 64


def test_execute_cli_blocks_when_backup_missing(reset_database: ResetDatabase) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)
    missing = reset_database.backup_path.parent / "missing.backup"

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            missing,
            reset_database.receipt_path,
        ),
    )

    # Then
    assert code == "backup_missing"


def test_execute_cli_blocks_when_restore_receipt_hash_mismatch(
    reset_database: ResetDatabase,
) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)
    write_restore_receipt(
        reset_database.receipt_path,
        "0" * 64,
        reset_database.backup_path.stat().st_size,
        source_database=TARGET_DATABASE,
        source_fingerprint=fingerprint,
    )

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
        ),
    )

    # Then
    assert code == "restore_receipt_mismatch"


def test_execute_cli_blocks_when_active_writers(reset_database: ResetDatabase) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
            active_writer_count=1,
        ),
    )

    # Then
    assert code == "active_writers"


def test_execute_cli_blocks_when_confirmation_wrong(reset_database: ResetDatabase) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
            confirm_phrase="WRONG",
        ),
    )

    # Then
    assert code == "confirmation_mismatch"


def test_execute_cli_blocks_when_fingerprint_mismatch(reset_database: ResetDatabase) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
            expected_fingerprint="b" * 64,
        ),
    )

    # Then
    assert code == "fingerprint_mismatch"


def test_execute_cli_blocks_when_plan_sha256_mismatch(reset_database: ResetDatabase) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
            expected_plan_sha256="c" * 64,
        ),
    )

    # Then
    assert code == "plan_mismatch"


def test_execute_cli_blocks_when_unknown_foreign_key(reset_database: ResetDatabase) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)
    with reset_database.engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE cli_risk_reference (
                id bigint PRIMARY KEY,
                signal_id bigint REFERENCES raw_signals(id)
            )
        """))

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
        ),
    )

    # Then
    assert code == "foreign_key_drift"


def test_execute_cli_does_not_delete_when_plan_mismatch(reset_database: ResetDatabase) -> None:
    # Given: 未提供合规 preflight 计划摘要，核心必须在删除前硬阻断。
    fingerprint, _ = seed_and_preflight(reset_database)
    write_restore_receipt(
        reset_database.receipt_path,
        reset_database.backup_sha256,
        reset_database.backup_path.stat().st_size,
        source_database=TARGET_DATABASE,
        source_fingerprint=fingerprint,
    )

    # When
    code = run_cli_error(
        reset_database,
        *_execute_args(
            fingerprint,
            "0" * 64,
            reset_database.backup_path,
            reset_database.receipt_path,
            expected_plan_sha256="0" * 64,
        ),
    )

    # Then: plan 与当前计划不符 → 未删除任何行。
    assert code == "plan_mismatch"
    with reset_database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM raw_signals")) == 1


def test_execute_cli_nullable_delivery_summary_all_deleted(
    reset_database: ResetDatabase,
) -> None:
    # Given
    fingerprint, plan_sha256 = _prepare_execute(reset_database)

    # When
    completed = run_cli(
        reset_database,
        *_execute_args(
            fingerprint,
            plan_sha256,
            reset_database.backup_path,
            reset_database.receipt_path,
        ),
    )

    # Then: 无条件全删，含 alert_id IS NULL 的合并摘要。
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert dict(payload["deleted_counts"])["notification_deliveries"] == 2
    with reset_database.engine.connect() as connection:
        total = connection.scalar(text("SELECT count(*) FROM notification_deliveries"))
        assert total == 0
