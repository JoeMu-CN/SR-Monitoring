"""风险基线重建核心的库函数实现（单事务、可注入失败）。

``reset_risk_baseline`` 是全部门禁通过后，以单事务物理清空监控轨业务表的
唯一入口；dry-run 只盘点不删除，execute 必验证恢复收据/指纹/计划摘要。
"""

from __future__ import annotations

import json
from hashlib import sha256

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.maintenance.risk_baseline_catalog import (
    catalog_foreign_keys,
    claim_provenance,
    file_sha256,
    preserved_counts,
    sha256_manifest,
    table_counts,
)
from app.maintenance.risk_baseline_schema import (
    DELETE_ORDER,
    EXPECTED_FOREIGN_KEYS,
    ResetGateError,
    ResetRiskBaselineRequest,
    ResetRiskBaselineResult,
)


def validate_external_gates(request: ResetRiskBaselineRequest) -> None:
    """在执行任何连接前，对恢复收据/写进程/目标数据库做硬门禁校验。"""
    if request.environment != "test":
        raise ResetGateError("environment_mismatch")
    if request.target_database_name != "supplier_risk_reset_test":
        raise ResetGateError("database_mismatch")
    if not request.backup_path.is_file():
        raise ResetGateError("backup_missing")
    if file_sha256(request.backup_path) != request.backup_sha256:
        raise ResetGateError("backup_hash_mismatch")
    receipt = request.restore_receipt
    if not receipt.restored or receipt.backup_sha256 != request.backup_sha256:
        raise ResetGateError("restore_receipt_mismatch")
    if request.active_writer_count != 0:
        raise ResetGateError("active_writers")
    if request.approved_foreign_keys != EXPECTED_FOREIGN_KEYS:
        raise ResetGateError("foreign_key_approval_mismatch")


def reset_risk_baseline(request: ResetRiskBaselineRequest) -> ResetRiskBaselineResult:
    """盘点或在全部门禁通过后以单事务删除风险业务基线。"""
    validate_external_gates(request)
    url = make_url(request.database_url.get_secret_value())
    if url.database != request.target_database_name:
        raise ResetGateError("database_mismatch")
    expected_confirmation = f"RESET RISK BASELINE {request.target_database_name}"
    if not request.dry_run and request.confirmation_phrase != expected_confirmation:
        raise ResetGateError("confirmation_mismatch")

    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    try:
        with engine.begin() as connection:
            database_name = str(connection.scalar(text("SELECT current_database()")))
            if database_name != request.target_database_name:
                raise ResetGateError("database_mismatch")
            foreign_keys = catalog_foreign_keys(connection)
            if foreign_keys != EXPECTED_FOREIGN_KEYS:
                raise ResetGateError("foreign_key_drift")
            alembic_version = str(
                connection.scalar(text("SELECT version_num FROM alembic_version"))
            )
            identity = f"{url.host}:{url.port}/{database_name}|{alembic_version}"
            fingerprint = sha256(identity.encode()).hexdigest()
            provenance = claim_provenance(connection)
            provenance_document = [
                {"id": claim_id, "promoted_signal_id": signal_id}
                for claim_id, signal_id in provenance
            ]
            provenance_hash = sha256_manifest(provenance_document)
            planned = table_counts(connection, DELETE_ORDER)
            plan_document = {
                "database_fingerprint": fingerprint,
                "foreign_keys": [item.model_dump() for item in foreign_keys],
                "planned_counts": planned,
                "provenance_sha256": provenance_hash,
            }
            plan_hash = sha256(json.dumps(
                plan_document, separators=(",", ":"), sort_keys=True
            ).encode()).hexdigest()
            if not request.dry_run and request.approved_plan_hash != plan_hash:
                raise ResetGateError("dry_run_approval_mismatch")

            deleted: list[tuple[str, int]] = []
            if request.dry_run:
                deleted = [(table, 0) for table in DELETE_ORDER]
            else:
                preparer = connection.dialect.identifier_preparer
                for table in DELETE_ORDER:
                    result = connection.execute(text(f"DELETE FROM {preparer.quote(table)}"))
                    deleted.append((table, result.rowcount or 0))
                    if request.fail_after_table == table:
                        raise ResetGateError("injected_failure")
            preserved = preserved_counts(connection)
            promoted_claim_count = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM research_claims "
                        "WHERE promoted_signal_id IS NOT NULL"
                    )
                )
                or 0
            )
            return ResetRiskBaselineResult(
                dry_run=request.dry_run,
                database_name=database_name,
                database_fingerprint_sha256=fingerprint,
                alembic_version=alembic_version,
                planned_counts=planned,
                deleted_counts=tuple(deleted),
                claim_count=len(provenance),
                promoted_claim_count=promoted_claim_count,
                claim_provenance=provenance,
                claim_provenance_sha256=provenance_hash,
                preserved_counts=preserved,
                plan_sha256=plan_hash,
            )
    finally:
        engine.dispose()
