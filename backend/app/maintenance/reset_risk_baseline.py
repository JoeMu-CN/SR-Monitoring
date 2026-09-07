"""风险基线重建的公共入口与 PowerShell 适配 CLI。

execute 接受 ``--expected-fingerprint`` / ``--expected-plan-sha256`` / ``--backup-path`` /
``--restore-receipt-path`` / ``--active-writer-count``，并在删除前重新验证备份、恢复收据、
写进程、目标数据库、FK 集合、指纹与计划摘要；全程不打印 DATABASE_URL 或任何密钥。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import assert_never

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.maintenance.risk_baseline_catalog import (
    file_sha256,
    preserved_counts,
    target_snapshot,
)
from app.maintenance.risk_baseline_core import reset_risk_baseline
from app.maintenance.risk_baseline_schema import (
    ALLOWED_DATABASE_NAME,
    CONFIRMATION_PHRASE,
    DELETE_ORDER,
    ENVIRONMENT,
    EXECUTION_RECEIPT_SCHEMA,
    EXPECTED_FOREIGN_KEYS,
    PREFLIGHT_SCHEMA,
    RESTORE_DATABASE_PREFIX,
    RESTORE_RECEIPT_SCHEMA,
    BackupReceipt,
    CliRequest,
    ExecutionReceipt,
    ResetGateError,
    ResetRiskBaselineRequest,
    RestoreReceipt,
    TargetSnapshot,
)

__all__ = [
    "DELETE_ORDER",
    "EXPECTED_FOREIGN_KEYS",
    "BackupReceipt",
    "ResetGateError",
    "ResetRiskBaselineRequest",
    "reset_risk_baseline",
]

PREFLIGHT_MODE = "preflight"


def _load_restore_receipt(request: CliRequest) -> RestoreReceipt:
    path = request.restore_receipt_path
    if path is None or not path.is_file():
        raise ResetGateError("restore_receipt_mismatch")
    return RestoreReceipt.model_validate_json(path.read_text(encoding="utf-8"))


def _parse_cli() -> CliRequest:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("preflight", "execute"))
    parser.add_argument("--environment", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--confirm-phrase")
    parser.add_argument("--backup-path")
    parser.add_argument("--restore-receipt-path")
    parser.add_argument("--active-writer-count", type=int)
    return CliRequest.model_validate(vars(parser.parse_args()))


def _validate_cli_target(request: CliRequest) -> None:
    if request.environment != ENVIRONMENT:
        raise ResetGateError("environment_mismatch")
    if request.database_name != ALLOWED_DATABASE_NAME:
        raise ResetGateError("database_mismatch")


def _preflight_payload(snapshot: TargetSnapshot, mode: str) -> dict[str, object]:
    return {
        "schema": PREFLIGHT_SCHEMA,
        "status": "passed" if snapshot.fk_inventory.status == "passed" else "failed",
        "mode": mode,
        "target": snapshot.target.model_dump(mode="json"),
        "fk_inventory": snapshot.fk_inventory.model_dump(mode="json"),
        "plan_sha256": snapshot.plan_sha256,
    }


def _run_read_only(request: CliRequest, database_url: str) -> TargetSnapshot:
    url = make_url(database_url)
    if url.database != request.database_name:
        raise ResetGateError("database_mismatch")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return target_snapshot(connection, request.database_name)
    finally:
        engine.dispose()


def _validate_restore_receipt_gates(request: CliRequest) -> RestoreReceipt:
    backup_path = request.backup_path
    if backup_path is None or not backup_path.is_file():
        raise ResetGateError("backup_missing")
    receipt = _load_restore_receipt(request)
    if receipt.schema_name != RESTORE_RECEIPT_SCHEMA or receipt.status != "passed":
        raise ResetGateError("restore_receipt_mismatch")
    if file_sha256(backup_path) != receipt.backup.sha256:
        raise ResetGateError("restore_receipt_mismatch")
    if backup_path.stat().st_size != receipt.backup.size_bytes:
        raise ResetGateError("restore_receipt_mismatch")
    source = receipt.source_target
    if source.environment != ENVIRONMENT or source.database_name != request.database_name:
        raise ResetGateError("restore_receipt_mismatch")
    restored = receipt.restored_target
    if (
        restored.environment != ENVIRONMENT
        or not restored.database_name.startswith(RESTORE_DATABASE_PREFIX)
        or restored.database_name == request.database_name
    ):
        raise ResetGateError("restore_target_mismatch")
    if request.active_writer_count != 0:
        raise ResetGateError("active_writers")
    if request.confirm_phrase != CONFIRMATION_PHRASE:
        raise ResetGateError("confirmation_mismatch")
    return receipt


def _execute_cli(request: CliRequest, database_url: str) -> dict[str, object]:
    receipt = _validate_restore_receipt_gates(request)
    url = make_url(database_url)
    if url.database != request.database_name:
        raise ResetGateError("database_mismatch")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    try:
        with engine.begin() as connection:
            if str(connection.scalar(text("SELECT current_database()"))) != request.database_name:
                raise ResetGateError("database_mismatch")
            snapshot = target_snapshot(connection, request.database_name)
            if snapshot.fk_inventory.status != "passed":
                raise ResetGateError("foreign_key_drift")
            if (
                request.expected_fingerprint is None
                or snapshot.target.fingerprint != request.expected_fingerprint
            ):
                raise ResetGateError("fingerprint_mismatch")
            if receipt.source_target.fingerprint != snapshot.target.fingerprint:
                raise ResetGateError("fingerprint_mismatch")
            if (
                request.expected_plan_sha256 is None
                or snapshot.plan_sha256 != request.expected_plan_sha256
            ):
                raise ResetGateError("plan_mismatch")
            preparer = connection.dialect.identifier_preparer
            deleted: list[tuple[str, int]] = []
            for table in DELETE_ORDER:
                result = connection.execute(text(f"DELETE FROM {preparer.quote(table)}"))
                deleted.append((table, result.rowcount or 0))
            preserved = preserved_counts(connection)
            promoted_remaining = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM research_claims "
                        "WHERE promoted_signal_id IS NOT NULL"
                    )
                )
                or 0
            )
            return ExecutionReceipt.model_validate({
                "schema": EXECUTION_RECEIPT_SCHEMA,
                "status": "completed",
                "dry_run": False,
                "environment": ENVIRONMENT,
                "database_name": request.database_name,
                "pre_reset_fingerprint": snapshot.target.fingerprint,
                "pre_reset_plan_sha256": snapshot.plan_sha256,
                "deleted_counts": tuple(deleted),
                "preserved_counts": preserved,
                "claim_count": snapshot.target.critical_table_counts.research_claims,
                "promoted_claim_count": promoted_remaining,
                "claim_provenance_sha256": snapshot.claim_provenance_sha256,
            }).model_dump(mode="json", by_alias=True)
    finally:
        engine.dispose()


def main() -> int:
    """运行仅输出机器可读 JSON 的维护 CLI，成功返回 0，门禁/输入错误返回 2。"""
    try:
        request = _parse_cli()
        _validate_cli_target(request)
        from app.config import DATABASE_URL

        match request.operation:
            case "execute":
                payload = _execute_cli(request, DATABASE_URL)
            case "preflight":
                snapshot = _run_read_only(request, DATABASE_URL)
                payload = _preflight_payload(snapshot, PREFLIGHT_MODE)
            case unreachable:
                assert_never(unreachable)
        sys.stdout.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        return 0
    except (ResetGateError, ValidationError, SQLAlchemyError, OSError) as error:
        code = error.code if isinstance(error, ResetGateError) else "invalid_request"
        sys.stderr.write(json.dumps({"error": code}, separators=(",", ":")) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
