"""风险基线重建核心的 PostgreSQL catalog 只读盘点。

本模块只执行只读查询并从 catalog 构建类型化快照；不做任何删除。
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from sqlalchemy import Connection, text

from app.maintenance.risk_baseline_schema import (
    DELETE_ORDER,
    EXPECTED_FOREIGN_KEYS,
    FK_ALLOWLIST_POLICY,
    CriticalTableCounts,
    ForeignKeyInventory,
    ForeignKeyReference,
    ResetGateError,
    TargetDescriptor,
    TargetSnapshot,
)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def catalog_foreign_keys(connection: Connection) -> tuple[ForeignKeyReference, ...]:
    rows = connection.execute(text("""
        SELECT child.relname, child_col.attname, parent.relname, parent_col.attname,
               CASE fk.confdeltype WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL'
                    WHEN 'r' THEN 'RESTRICT' WHEN 'd' THEN 'SET DEFAULT'
                    ELSE 'NO ACTION' END
        FROM pg_constraint fk
        JOIN pg_class child ON child.oid = fk.conrelid
        JOIN pg_class parent ON parent.oid = fk.confrelid
        JOIN LATERAL unnest(fk.conkey) WITH ORDINALITY child_key(attnum, ord) ON true
        JOIN LATERAL unnest(fk.confkey) WITH ORDINALITY parent_key(attnum, ord)
          ON parent_key.ord = child_key.ord
        JOIN pg_attribute child_col
          ON child_col.attrelid = child.oid AND child_col.attnum = child_key.attnum
        JOIN pg_attribute parent_col
          ON parent_col.attrelid = parent.oid AND parent_col.attnum = parent_key.attnum
        WHERE fk.contype = 'f' AND parent.relname = ANY(:targets)
        ORDER BY child.relname, child_col.attname, parent.relname, parent_col.attname
    """), {"targets": list(DELETE_ORDER)})
    return tuple(
        ForeignKeyReference(
            child_table=row[0],
            child_column=row[1],
            parent_table=row[2],
            parent_column=row[3],
            on_delete=row[4],
        )
        for row in rows
    )


def table_counts(connection: Connection, tables: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    preparer = connection.dialect.identifier_preparer
    return tuple(
        (table, int(connection.scalar(text(f"SELECT count(*) FROM {preparer.quote(table)}")) or 0))
        for table in tables
    )


def preserved_counts(connection: Connection) -> tuple[tuple[str, int], ...]:
    names = tuple(connection.scalars(text("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> ALL(:deleted)
          AND tablename <> 'alembic_version'
        ORDER BY tablename
    """), {"deleted": list(DELETE_ORDER)}))
    return table_counts(connection, names)


def claim_provenance(connection: Connection) -> tuple[tuple[int, int | None], ...]:
    rows = connection.execute(
        text("SELECT id, promoted_signal_id FROM research_claims ORDER BY id")
    )
    return tuple(
        (int(row[0]), None if row[1] is None else int(row[1])) for row in rows
    )


def foreign_key_edge(reference: ForeignKeyReference) -> str:
    return (
        f"{reference.child_table}.{reference.child_column} -> "
        f"{reference.parent_table}.{reference.parent_column} "
        f"ON DELETE {reference.on_delete}"
    )


def sha256_manifest(claims_document: list[dict[str, int | None]]) -> str:
    return sha256(json.dumps(
        claims_document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()


def target_snapshot(connection: Connection, database_name: str) -> TargetSnapshot:
    actual_database = str(connection.scalar(text("SELECT current_database()")))
    if actual_database != database_name:
        raise ResetGateError("database_mismatch")
    observed = catalog_foreign_keys(connection)
    observed_keys = set(observed)
    expected_keys = set(EXPECTED_FOREIGN_KEYS)
    missing = tuple(item for item in EXPECTED_FOREIGN_KEYS if item not in observed_keys)
    unexpected = tuple(item for item in observed if item not in expected_keys)
    migration = str(connection.scalar(text("SELECT version_num FROM alembic_version")))
    counts = dict(table_counts(connection, DELETE_ORDER))
    claim_count = int(connection.scalar(text("SELECT count(*) FROM research_claims")) or 0)
    promoted_count = int(connection.scalar(text(
        "SELECT count(*) FROM research_claims WHERE promoted_signal_id IS NOT NULL"
    )) or 0)
    critical_counts = CriticalTableCounts(
        raw_signals=counts["raw_signals"],
        ai_analysis_records=counts["ai_analysis_records"],
        risk_events=counts["risk_events"],
        risk_alerts=counts["risk_alerts"],
        notification_deliveries=counts["notification_deliveries"],
        collection_runs=counts["collection_runs"],
        source_member_states=counts["source_member_states"],
        research_claims=claim_count,
        research_claims_promoted_nonnull=promoted_count,
    )
    claims = claim_provenance(connection)
    claim_hash = sha256_manifest(
        [{"id": claim_id, "promoted_signal_id": signal_id} for claim_id, signal_id in claims]
    )
    content_document = {
        "migration_version": migration,
        "critical_table_counts": critical_counts.model_dump(),
        "research_claim_promoted_link_manifest_sha256": claim_hash,
    }
    content_hash = sha256(json.dumps(
        content_document, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()
    fingerprint = sha256(json.dumps(
        {"database_name": database_name, "content_fingerprint": content_hash},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()
    observed_edges = tuple(foreign_key_edge(item) for item in observed)
    inventory = ForeignKeyInventory(
        policy=FK_ALLOWLIST_POLICY,
        status="passed" if not missing and not unexpected else "failed",
        observed_edges=observed_edges,
        missing_edges=tuple(foreign_key_edge(item) for item in missing),
        unexpected_edges=tuple(foreign_key_edge(item) for item in unexpected),
    )
    plan_hash = sha256(json.dumps(
        {"fingerprint": fingerprint, "observed_edges": observed_edges},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()
    return TargetSnapshot(
        target=TargetDescriptor(
            environment="test",
            database_name=database_name,
            fingerprint=fingerprint,
            content_fingerprint=content_hash,
            migration_version=migration,
            critical_table_counts=critical_counts,
        ),
        fk_inventory=inventory,
        plan_sha256=plan_hash,
        claim_provenance_sha256=claim_hash,
    )
