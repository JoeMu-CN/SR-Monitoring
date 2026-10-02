"""迁移 0055（``usgs-earthquake-day`` 转内置采集）定向验证。

沿用 ``test_tyc_profile_validity_migration.py`` 与 ``mofcom_migration_support`` 的
隔离库模式：在隔离测试库内跑真实 alembic upgrade/downgrade 往返，绝不连接或写入
当前业务库。

契约：
- upgrade：行存在时把原 ``adapter_status/adapter_config/endpoint_url/adapter_version/
  adapter_published_at`` 写进 ``login_config.usgs_builtin_migration`` provenance marker，
  再置 ``adapter_status='builtin'``、``adapter_config='{}'``、``endpoint_url`` 为固定
  官方地址；不修改 ``updated_at``，保留 enabled/schedule 与全部历史；行不存在时按
  规范插入（``official_api``、可信度 95、``*/30 * * * *``、默认停用、``was_inserted=true``）。
- 已是目标态（builtin + 空配置 + 固定地址）的行 0 行更新、不写 marker，重复执行幂等。
- downgrade：``was_inserted=false`` 的有效 marker 逐字段条件还原（仅当前值仍等于迁移
  写入值才还原）并清理 marker；``was_inserted=true`` 的行仅在仍停用且下列 5 张引用表
  全部无引用时删除——``collection_runs``/``source_member_states``/``raw_signals``
  （CASCADE）与 ``data_source_audit_logs``/``source_onboarding_drafts``（SET NULL）；
  任一表有引用则保留行与 marker，并把不可自动降级原因写入 marker；缺行 no-op。
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from mofcom_migration_support import (
    SourceSeed,
    insert_source,
    isolated_database,
    run_alembic,
)
from sqlalchemy import Connection, create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = BACKEND_ROOT / "alembic" / "versions" / "0055_usgs_builtin_source.py"
USGS_CODE = "usgs-earthquake-day"

ORIGINAL_CONFIG: dict[str, object] = {
    "format": "json",
    "items_path": "features",
    "mapping": {
        "external_id": "id",
        "title": "properties.title",
        "content": "properties.place",
        "url": "properties.url",
        "published_at": None,
    },
    "fingerprint_fields": ["external_id", "title"],
}
ORIGINAL_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/legacy.geojson"
ORIGINAL_VERSION = 7
ORIGINAL_PUBLISHED_AT = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
ORIGINAL_UPDATED_AT = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)

# 5 张引用 data_sources.id 的表各一条最小合法引用；SET NULL 表也算引用，必须阻止删除。
REFERENCE_SEEDS: dict[str, tuple[str, str]] = {
    "collection_runs": (
        "has_collection_runs",
        "INSERT INTO collection_runs (source_id, status) VALUES (:source_id, 'succeeded')",
    ),
    "source_member_states": (
        "has_source_member_states",
        "INSERT INTO source_member_states "
        "(source_id, member_key, first_seen_at, last_seen_at, baseline_snapshot_hash) "
        "VALUES (:source_id, 'member-1', now(), now(), 'baseline-hash-1')",
    ),
    "raw_signals": (
        "has_raw_signals",
        "INSERT INTO raw_signals (source_id, title, content, fingerprint, raw_data) "
        "VALUES (:source_id, '地震速报', '南大西洋', 'fingerprint-1', '{}'::jsonb)",
    ),
    "data_source_audit_logs": (
        "has_data_source_audit_logs",
        "INSERT INTO data_source_audit_logs (source_id, action, actor_role, changes) "
        "VALUES (:source_id, 'update', 'risk_admin', '{}'::jsonb)",
    ),
    "source_onboarding_drafts": (
        "has_onboarding_drafts",
        "INSERT INTO source_onboarding_drafts (source_id) VALUES (:source_id)",
    ),
}


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0055", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration_module()
MIGRATION_MARKER: str = _MIGRATION.MIGRATION_MARKER
MARKER_KEY: str = _MIGRATION.MARKER_KEY
USGS_ENDPOINT_URL: str = _MIGRATION.USGS_ENDPOINT_URL
USGS_SCHEDULE: str = _MIGRATION.USGS_SCHEDULE
USGS_SOURCE_TYPE: str = _MIGRATION.USGS_SOURCE_TYPE
USGS_CREDIBILITY: int = _MIGRATION.USGS_CREDIBILITY


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def _row(connection: Connection, code: str = USGS_CODE) -> dict[str, object] | None:
    row = (
        connection.execute(
            text(
                "SELECT id, source_type, credibility, enabled, schedule, endpoint_url, "
                "adapter_status, adapter_config, adapter_version, adapter_published_at, "
                "updated_at, login_config "
                "FROM data_sources WHERE code = :code"
            ),
            {"code": code},
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


def _marker(connection: Connection, code: str = USGS_CODE) -> dict[str, object] | None:
    row = _row(connection, code)
    if row is None:
        return None
    config = row["login_config"]
    if not isinstance(config, dict):
        return None
    marker = config.get(MARKER_KEY)
    return marker if isinstance(marker, dict) else None


def _count(connection: Connection, code: str = USGS_CODE) -> int:
    return int(
        connection.scalar(
            text("SELECT count(*) FROM data_sources WHERE code = :code"), {"code": code}
        )
    )


def _source_id(connection: Connection) -> int:
    return int(
        connection.scalar(
            text("SELECT id FROM data_sources WHERE code = :code"), {"code": USGS_CODE}
        )
    )


def _seed_existing_usgs(
    connection: Connection,
    *,
    adapter_status: str = "published",
    endpoint_url: str = ORIGINAL_URL,
    adapter_config: dict[str, object] | None = None,
    enabled: bool = True,
    schedule: str = "0 3 * * *",
) -> int:
    """在 0054 前态插入 usgs 行，并固定 adapter 元数据与 updated_at。"""
    source_id = insert_source(
        connection,
        SourceSeed(
            code=USGS_CODE,
            schedule=schedule,
            enabled=enabled,
            adapter_status=adapter_status,
            endpoint_url=endpoint_url,
            adapter_config=json.dumps(
                ORIGINAL_CONFIG if adapter_config is None else adapter_config
            ),
        ),
    )
    connection.execute(
        text(
            "UPDATE data_sources SET adapter_version = :version, "
            "adapter_published_at = :published_at, updated_at = :updated_at "
            "WHERE id = :id"
        ),
        {
            "id": source_id,
            "version": ORIGINAL_VERSION,
            "published_at": ORIGINAL_PUBLISHED_AT,
            "updated_at": ORIGINAL_UPDATED_AT,
        },
    )
    return source_id


def _marker_published_at(marker: dict[str, object]) -> datetime:
    before = marker["before"]
    assert isinstance(before, dict)
    raw = before["adapter_published_at"]
    assert isinstance(raw, str)
    return datetime.fromisoformat(raw)


def test_fresh_database_upgrade_inserts_disabled_builtin_row(
    migration_database: str,
) -> None:
    # Given：空库 fresh 链（仓库无创建 usgs-earthquake-day 的迁移）。
    # When：升级到 0055。
    run_alembic(migration_database, "upgrade", "0055")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        row = _row(connection)
        marker = _marker(connection)
    engine.dispose()

    # Then：按规范插入默认停用的内置信源；marker 记录 was_inserted=true。
    assert row is not None
    assert row["source_type"] == USGS_SOURCE_TYPE == "official_api"
    assert row["credibility"] == USGS_CREDIBILITY == 95
    assert row["schedule"] == USGS_SCHEDULE == "*/30 * * * *"
    assert row["enabled"] is False
    assert row["endpoint_url"] == USGS_ENDPOINT_URL
    assert row["adapter_status"] == "builtin"
    assert row["adapter_config"] == {}
    assert row["adapter_version"] == 0
    assert row["adapter_published_at"] is None
    assert marker == {"revision": MIGRATION_MARKER, "was_inserted": True}


def test_existing_published_row_converts_without_touching_metadata(
    migration_database: str,
) -> None:
    # Given：0054 上已有 published 声明式行，enabled/schedule/adapter 元数据/updated_at 固定。
    run_alembic(migration_database, "upgrade", "0054")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_usgs(connection)

    # When：升级到 0055。
    run_alembic(migration_database, "upgrade", "0055")
    with engine.connect() as connection:
        row = _row(connection)
        marker = _marker(connection)
    engine.dispose()

    # Then：转 builtin、清配置、固定地址；enabled/schedule/updated_at 与历史元数据不变。
    assert row is not None
    assert row["adapter_status"] == "builtin"
    assert row["adapter_config"] == {}
    assert row["endpoint_url"] == USGS_ENDPOINT_URL
    assert row["enabled"] is True
    assert row["schedule"] == "0 3 * * *"
    assert row["updated_at"] == ORIGINAL_UPDATED_AT
    assert row["adapter_version"] == ORIGINAL_VERSION
    assert row["adapter_published_at"] == ORIGINAL_PUBLISHED_AT
    # Then：marker 记录完整前态（含未修改的 version/published_at）。
    assert marker is not None
    assert marker["revision"] == MIGRATION_MARKER
    assert marker["was_inserted"] is False
    before = marker["before"]
    assert isinstance(before, dict)
    assert before["adapter_status"] == "published"
    assert before["adapter_config"] == ORIGINAL_CONFIG
    assert before["endpoint_url"] == ORIGINAL_URL
    assert before["adapter_version"] == ORIGINAL_VERSION
    assert _marker_published_at(marker) == ORIGINAL_PUBLISHED_AT


def test_downgrade_restores_original_values_and_removes_marker(
    migration_database: str,
) -> None:
    # Given：published 前态升级到 0055。
    run_alembic(migration_database, "upgrade", "0054")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_usgs(connection)
    run_alembic(migration_database, "upgrade", "0055")

    # When：降级到 0054。
    run_alembic(migration_database, "downgrade", "0054")
    with engine.connect() as connection:
        row = _row(connection)
        marker = _marker(connection)
    engine.dispose()

    # Then：前态逐字段精确还原、marker 清理；enabled/schedule/updated_at 不被顺手改写。
    assert row is not None
    assert row["adapter_status"] == "published"
    assert row["adapter_config"] == ORIGINAL_CONFIG
    assert row["endpoint_url"] == ORIGINAL_URL
    assert row["adapter_version"] == ORIGINAL_VERSION
    assert row["adapter_published_at"] == ORIGINAL_PUBLISHED_AT
    assert row["enabled"] is True
    assert row["schedule"] == "0 3 * * *"
    assert row["updated_at"] == ORIGINAL_UPDATED_AT
    assert marker is None


def test_downgrade_deletes_inserted_row_without_references(
    migration_database: str,
) -> None:
    # Given：fresh 库由 0055 新建默认停用行，无任何引用。
    run_alembic(migration_database, "upgrade", "0055")

    # When：降级到 0054；Then：行被删除且不报错。
    run_alembic(migration_database, "downgrade", "0054")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        count = _count(connection)
    engine.dispose()
    assert count == 0


@pytest.mark.parametrize("table", sorted(REFERENCE_SEEDS))
def test_downgrade_keeps_inserted_row_when_referenced(
    migration_database: str, table: str
) -> None:
    # Given：0055 新建行被分别置入 5 张引用表各一条记录（含 SET NULL 的审计/草稿）。
    flag, seed_sql = REFERENCE_SEEDS[table]
    run_alembic(migration_database, "upgrade", "0055")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        source_id = _source_id(connection)
        connection.execute(text(seed_sql), {"source_id": source_id})

    # When：降级到 0054。
    run_alembic(migration_database, "downgrade", "0054")
    with engine.connect() as connection:
        row = _row(connection)
        marker = _marker(connection)
        count = _count(connection)
    engine.dispose()

    # Then：行与 marker 均保留，并记录不可自动降级原因；引用不被删除或置空。
    assert count == 1
    assert row is not None
    assert row["adapter_status"] == "builtin"
    assert marker is not None
    assert marker["revision"] == MIGRATION_MARKER
    assert marker["was_inserted"] is True
    blocked = marker["downgrade_blocked"]
    assert isinstance(blocked, dict)
    assert isinstance(blocked["reason"], str) and blocked["reason"]
    assert blocked["enabled"] is False
    assert blocked[flag] is True
    # 其它未被引用的表对应标志必须为 false。
    for other_flag, _ in REFERENCE_SEEDS.values():
        if other_flag != flag:
            assert blocked[other_flag] is False


def test_upgrade_is_noop_when_already_at_target_state(migration_database: str) -> None:
    # Given：0054 上 usgs 行已是目标态（builtin、空配置、固定地址），但 enabled/schedule 自定义。
    run_alembic(migration_database, "upgrade", "0054")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_usgs(
            connection,
            adapter_status="builtin",
            endpoint_url=USGS_ENDPOINT_URL,
            adapter_config={},
            enabled=False,
            schedule="15 * * * *",
        )

    # When：升级到 0055。
    run_alembic(migration_database, "upgrade", "0055")
    with engine.connect() as connection:
        row = _row(connection)
        marker = _marker(connection)
    engine.dispose()

    # Then：幂等 no-op——不写 marker、不改 enabled/schedule/updated_at。
    assert row is not None
    assert row["adapter_status"] == "builtin"
    assert row["adapter_config"] == {}
    assert row["endpoint_url"] == USGS_ENDPOINT_URL
    assert row["enabled"] is False
    assert row["schedule"] == "15 * * * *"
    assert row["updated_at"] == ORIGINAL_UPDATED_AT
    assert marker is None


def test_downgrade_keeps_operator_rewritten_fields(migration_database: str) -> None:
    # Given：升级到 0055 后运营再次改写状态与配置（端点仍等于迁移写入值）。
    run_alembic(migration_database, "upgrade", "0054")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_usgs(connection)
    run_alembic(migration_database, "upgrade", "0055")
    rewritten_config = {"format": "json", "items_path": "custom.features"}
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_sources SET adapter_status = 'published', "
                "adapter_config = CAST(:config AS jsonb) WHERE code = :code"
            ),
            {"code": USGS_CODE, "config": json.dumps(rewritten_config)},
        )

    # When：降级到 0054。
    run_alembic(migration_database, "downgrade", "0054")
    with engine.connect() as connection:
        row = _row(connection)
        marker = _marker(connection)
    engine.dispose()

    # Then：已改写字段保留现值；仍等于迁移写入值的端点还原；有效 marker 清理。
    assert row is not None
    assert row["adapter_status"] == "published"
    assert row["adapter_config"] == rewritten_config
    assert row["endpoint_url"] == ORIGINAL_URL
    assert marker is None


def test_downgrade_missing_row_is_noop(migration_database: str) -> None:
    # Given：升级到 0055 后删除目标行。
    run_alembic(migration_database, "upgrade", "0055")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM data_sources WHERE code = :code"), {"code": USGS_CODE}
        )

    # When / Then：降级到 0054 不报错且不重建行。
    run_alembic(migration_database, "downgrade", "0054")
    with engine.connect() as connection:
        count = _count(connection)
    engine.dispose()
    assert count == 0
