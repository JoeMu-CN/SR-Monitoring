"""迁移 0062（``commodity-futures``/``fx-rates``/``semiconductor-index`` 行情信源播种）定向验证。

沿用 ``test_usgs_builtin_migration.py`` 与 ``mofcom_migration_support`` 的隔离库模式：在
隔离测试库内跑真实 alembic upgrade/downgrade 往返，绝不连接或写入当前业务库。

契约：
- upgrade：行缺失时按规范插入默认停用的内置行情行（``market_data``、可信度 80、
  ``0 8 * * *``、``was_inserted=true`` marker）；行存在且不在目标态时把原
  ``adapter_status/adapter_config/endpoint_url/adapter_version/adapter_published_at``
  写进 ``login_config.market_sources_migration`` provenance marker，再归一为 builtin +
  空配置 + 固定 endpoint；**不修改** ``enabled``/``schedule``/``updated_at``/``source_type``。
- 迁移永不启用信源，也不覆盖运营自定义的启停与周期。
- downgrade：``was_inserted=true`` 的行仅在仍停用且 5 张引用表全部无引用时删除——
  ``collection_runs``/``source_member_states``/``raw_signals``（CASCADE）与
  ``data_source_audit_logs``/``source_onboarding_drafts``（SET NULL）；有引用或已启用的
  行保留行与 marker，并把不可自动降级原因写入 ``downgrade_blocked``；``was_inserted=false``
  的有效 marker 逐字段条件还原并清理；缺行 no-op。
- 播种的固定 endpoint 必须与 ``app.signals.sources`` 三个适配器的真实 endpoint 逐字一致，
  否则内置采集与 ``app.market.registry`` 的绑定会在运行期静默失效。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
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
MIGRATION_FILE = BACKEND_ROOT / "alembic" / "versions" / "0062_market_sources.py"

CODES: tuple[str, ...] = ("commodity-futures", "fx-rates", "semiconductor-index")

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
        "INSERT INTO raw_signals "
        "(source_id, title, content, fingerprint, dedup_key, raw_data) "
        "VALUES (:source_id, '期货主力行情', '沪铜 72000', 'fingerprint-1', "
        "'dedup-key-1', '{}'::jsonb)",
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

ORIGINAL_CONFIG: dict[str, object] = {
    "format": "json",
    "items_path": "custom.payload",
    "mapping": {"external_id": "code", "title": "label"},
}
ORIGINAL_URL = "https://example.invalid/legacy-market-feed"
ORIGINAL_VERSION = 9
ORIGINAL_PUBLISHED_AT = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
ORIGINAL_UPDATED_AT = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
OPERATIONS_SOURCE_TYPE = "market_data_custom"
OPERATIONS_NAME = "运营自定义行情信源"
OPERATIONS_CREDIBILITY = 55
OPERATIONS_SCHEDULE = "*/30 * * * *"


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0062", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration_module()
MIGRATION_MARKER: str = _MIGRATION.MIGRATION_MARKER
MARKER_KEY: str = _MIGRATION.MARKER_KEY
ENDPOINTS: dict[str, str] = dict(_MIGRATION.ENDPOINTS)
NAMES: dict[str, str] = dict(_MIGRATION.NAMES)


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def _row(connection: Connection, code: str) -> dict[str, object] | None:
    row = (
        connection.execute(
            text(
                "SELECT name, source_type, credibility, schedule, enabled, endpoint_url, "
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


def _marker(connection: Connection, code: str) -> dict[str, object] | None:
    row = _row(connection, code)
    if row is None:
        return None
    config = row["login_config"]
    if not isinstance(config, dict):
        return None
    marker = config.get(MARKER_KEY)
    return marker if isinstance(marker, dict) else None


def _count(connection: Connection, code: str) -> int:
    return int(
        connection.scalar(
            text("SELECT count(*) FROM data_sources WHERE code = :code"), {"code": code}
        )
    )


def _source_id(connection: Connection, code: str) -> int:
    return int(
        connection.scalar(
            text("SELECT id FROM data_sources WHERE code = :code"), {"code": code}
        )
    )


def _seed_existing_market(
    connection: Connection,
    code: str,
    *,
    adapter_status: str = "published",
    endpoint_url: str = ORIGINAL_URL,
    adapter_config: dict[str, object] | None = None,
    enabled: bool = True,
    schedule: str = OPERATIONS_SCHEDULE,
) -> int:
    """在 0061 前态插入一行自定义行情信源，并固定运营自定义字段与 updated_at。"""
    source_id = insert_source(
        connection,
        SourceSeed(
            code=code,
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
            "UPDATE data_sources SET name = :name, source_type = :source_type, "
            "credibility = :credibility, adapter_version = :version, "
            "adapter_published_at = :published_at, updated_at = :updated_at "
            "WHERE id = :id"
        ),
        {
            "id": source_id,
            "name": OPERATIONS_NAME,
            "source_type": OPERATIONS_SOURCE_TYPE,
            "credibility": OPERATIONS_CREDIBILITY,
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


def _alembic_output(database_url: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_seeded_endpoints_match_real_adapter_endpoints() -> None:
    # Given / When：直接取内置适配器声明的 endpoint。
    from app.signals.sources import (
        CommodityFuturesAdapter,
        FxRatesAdapter,
        SemiconductorIndexAdapter,
    )

    # Then：迁移播种的固定地址必须与代码里的真实 endpoint 逐字一致，否则采集静默失效。
    assert ENDPOINTS == {
        "commodity-futures": CommodityFuturesAdapter.endpoint,
        "fx-rates": FxRatesAdapter.endpoint,
        "semiconductor-index": SemiconductorIndexAdapter.endpoint,
    }


@pytest.mark.parametrize("code", CODES)
def test_fresh_upgrade_seeds_disabled_builtin_market_row(
    migration_database: str, code: str
) -> None:
    # Given：空库 fresh 链（仓库无创建三个行情信源的迁移）。
    # When：升级到 0062。
    run_alembic(migration_database, "upgrade", "0062")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        row = _row(connection, code)
        marker = _marker(connection, code)
    engine.dispose()

    # Then：按规范插入默认停用的内置行情信源；marker 记录 was_inserted=true。
    assert row is not None
    assert row["name"] == NAMES[code]
    assert row["source_type"] == "market_data"
    assert row["credibility"] == 80
    assert row["schedule"] == "0 8 * * *"
    assert row["enabled"] is False
    assert row["endpoint_url"] == ENDPOINTS[code]
    assert row["adapter_status"] == "builtin"
    assert row["adapter_config"] == {}
    assert row["adapter_version"] == 0
    assert row["adapter_published_at"] is None
    assert marker == {"revision": MIGRATION_MARKER, "was_inserted": True}


def test_existing_custom_row_normalized_without_touching_operations_fields(
    migration_database: str,
) -> None:
    # Given：0061 上已有一行 published 声明式行情信源，运营自定义了名称/类型/可信度/
    # 周期/启停，且 adapter 元数据与 updated_at 固定；另两个 code 仍缺失。
    run_alembic(migration_database, "upgrade", "0061")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_market(connection, "fx-rates")

    # When：升级到 0062。
    run_alembic(migration_database, "upgrade", "0062")
    with engine.connect() as connection:
        row = _row(connection, "fx-rates")
        marker = _marker(connection, "fx-rates")
        inserted = [_count(connection, code) for code in CODES if code != "fx-rates"]
    engine.dispose()

    # Then：转 builtin、清配置、固定地址；运营字段与 updated_at 一律不被顺手改写。
    assert row is not None
    assert row["adapter_status"] == "builtin"
    assert row["adapter_config"] == {}
    assert row["endpoint_url"] == ENDPOINTS["fx-rates"]
    assert row["enabled"] is True
    assert row["schedule"] == OPERATIONS_SCHEDULE
    assert row["source_type"] == OPERATIONS_SOURCE_TYPE
    assert row["name"] == OPERATIONS_NAME
    assert row["credibility"] == OPERATIONS_CREDIBILITY
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
    # Then：同批次里缺失的两个 code 仍按规范插入。
    assert inserted == [1, 1]


def test_upgrade_is_idempotent_when_executed_twice(
    migration_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given：0061 上的既有自定义行 + 缺失行混合前态。
    run_alembic(migration_database, "upgrade", "0061")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_market(connection, "fx-rates")

    # When：直接重放 upgrade() 两次（模拟陈旧状态下重复执行，不改 alembic_version）。
    # 用真实 Operations 替换模块级 op 代理，避免依赖 alembic 的私有 proxy 安装接口。
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(_MIGRATION, "op", operations)
        _MIGRATION.upgrade()
        _MIGRATION.upgrade()

    with engine.connect() as connection:
        rows = {code: _row(connection, code) for code in CODES}
        markers = {code: _marker(connection, code) for code in CODES}
    engine.dispose()

    # Then：每 code 仍恰好一行，既有行的 marker 仍是 was_inserted=false（不被覆盖丢失前态）。
    assert all(row is not None for row in rows.values())
    assert markers["fx-rates"] is not None
    assert markers["fx-rates"]["was_inserted"] is False
    before = markers["fx-rates"]["before"]
    assert isinstance(before, dict)
    assert before["endpoint_url"] == ORIGINAL_URL
    for code in ("commodity-futures", "semiconductor-index"):
        assert markers[code] == {"revision": MIGRATION_MARKER, "was_inserted": True}
    # Then：既有行的运营字段仍保持自定义值。
    assert rows["fx-rates"] is not None
    assert rows["fx-rates"]["enabled"] is True
    assert rows["fx-rates"]["schedule"] == OPERATIONS_SCHEDULE
    assert rows["fx-rates"]["source_type"] == OPERATIONS_SOURCE_TYPE


def test_upgrade_is_noop_when_already_at_target_state(migration_database: str) -> None:
    # Given：0061 上行已是目标态（builtin、空配置、固定地址），但 enabled/schedule 自定义。
    run_alembic(migration_database, "upgrade", "0061")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_market(
            connection,
            "commodity-futures",
            adapter_status="builtin",
            endpoint_url=ENDPOINTS["commodity-futures"],
            adapter_config={},
            enabled=False,
            schedule="15 * * * *",
        )

    # When：升级到 0062。
    run_alembic(migration_database, "upgrade", "0062")
    with engine.connect() as connection:
        row = _row(connection, "commodity-futures")
        marker = _marker(connection, "commodity-futures")
    engine.dispose()

    # Then：幂等 no-op——不写 marker、不改 enabled/schedule/updated_at。
    assert row is not None
    assert row["adapter_status"] == "builtin"
    assert row["adapter_config"] == {}
    assert row["endpoint_url"] == ENDPOINTS["commodity-futures"]
    assert row["enabled"] is False
    assert row["schedule"] == "15 * * * *"
    assert row["updated_at"] == ORIGINAL_UPDATED_AT
    assert marker is None


def test_downgrade_deletes_seeded_rows_without_references(
    migration_database: str,
) -> None:
    # Given：fresh 库由 0062 新建三行默认停用行情信源，无任何引用。
    run_alembic(migration_database, "upgrade", "0062")

    # When：降级到 0061；Then：三行全部删除且不报错。
    run_alembic(migration_database, "downgrade", "0061")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        counts = {code: _count(connection, code) for code in CODES}
    engine.dispose()
    assert counts == dict.fromkeys(CODES, 0)


@pytest.mark.parametrize("table", sorted(REFERENCE_SEEDS))
def test_downgrade_keeps_referenced_seeded_row_with_blocked_marker(
    migration_database: str, table: str
) -> None:
    # Given：0062 新建的 commodity-futures 行被置入 5 张引用表各一条记录之一。
    flag, seed_sql = REFERENCE_SEEDS[table]
    run_alembic(migration_database, "upgrade", "0062")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        source_id = _source_id(connection, "commodity-futures")
        connection.execute(text(seed_sql), {"source_id": source_id})

    # When：降级到 0061。
    run_alembic(migration_database, "downgrade", "0061")
    with engine.connect() as connection:
        row = _row(connection, "commodity-futures")
        marker = _marker(connection, "commodity-futures")
        untouched = {code: _count(connection, code) for code in CODES[1:]}
    engine.dispose()

    # Then：行与 marker 均保留并记录不可自动降级原因；无引用的另外两行照常删除。
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
    for other_flag, _ in REFERENCE_SEEDS.values():
        if other_flag != flag:
            assert blocked[other_flag] is False
    assert untouched == dict.fromkeys(CODES[1:], 0)


def test_downgrade_keeps_enabled_seeded_row_with_blocked_marker(
    migration_database: str,
) -> None:
    # Given：0062 新建行在 downgrade 前被运营启用（无任何引用）。
    run_alembic(migration_database, "upgrade", "0062")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE data_sources SET enabled = true WHERE code = :code"),
            {"code": "semiconductor-index"},
        )

    # When：降级到 0061。
    run_alembic(migration_database, "downgrade", "0061")
    with engine.connect() as connection:
        row = _row(connection, "semiconductor-index")
        marker = _marker(connection, "semiconductor-index")
        deleted = _count(connection, "commodity-futures")
    engine.dispose()

    # Then：已启用行保留并标记 enabled=true；未启用的其它行仍被删除。
    assert row is not None
    assert row["enabled"] is True
    assert marker is not None
    blocked = marker["downgrade_blocked"]
    assert isinstance(blocked, dict)
    assert blocked["enabled"] is True
    assert all(
        blocked[other_flag] is False for other_flag, _ in REFERENCE_SEEDS.values()
    )
    assert deleted == 0


def test_downgrade_restores_normalized_existing_row_and_clears_marker(
    migration_database: str,
) -> None:
    # Given：published 前态升级到 0062。
    run_alembic(migration_database, "upgrade", "0061")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_existing_market(connection, "fx-rates")
    run_alembic(migration_database, "upgrade", "0062")

    # When：降级到 0061。
    run_alembic(migration_database, "downgrade", "0061")
    with engine.connect() as connection:
        row = _row(connection, "fx-rates")
        marker = _marker(connection, "fx-rates")
    engine.dispose()

    # Then：前态逐字段精确还原、marker 清理；运营字段不被顺手改写。
    assert row is not None
    assert row["adapter_status"] == "published"
    assert row["adapter_config"] == ORIGINAL_CONFIG
    assert row["endpoint_url"] == ORIGINAL_URL
    assert row["adapter_version"] == ORIGINAL_VERSION
    assert row["adapter_published_at"] == ORIGINAL_PUBLISHED_AT
    assert row["enabled"] is True
    assert row["schedule"] == OPERATIONS_SCHEDULE
    assert row["source_type"] == OPERATIONS_SOURCE_TYPE
    assert row["credibility"] == OPERATIONS_CREDIBILITY
    assert row["updated_at"] == ORIGINAL_UPDATED_AT
    assert marker is None


def test_downgrade_missing_row_is_noop(migration_database: str) -> None:
    # Given：升级到 0062 后删除目标行。
    run_alembic(migration_database, "upgrade", "0062")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM data_sources WHERE code = :code"),
            {"code": "fx-rates"},
        )

    # When / Then：降级到 0061 不报错且不重建行。
    run_alembic(migration_database, "downgrade", "0061")
    with engine.connect() as connection:
        count = _count(connection, "fx-rates")
    engine.dispose()
    assert count == 0


def test_roundtrip_downgrade_0062_then_upgrade_head(migration_database: str) -> None:
    # Given：升级到 0062，三行已播种。
    run_alembic(migration_database, "upgrade", "0062")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        before = {code: _row(connection, code) for code in CODES}
    engine.dispose()

    # When：降级到 0061 再升级到 head。
    run_alembic(migration_database, "downgrade", "0061")
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        after = {code: _row(connection, code) for code in CODES}
        markers = {code: _marker(connection, code) for code in CODES}
    engine.dispose()

    # Then：往返后 head 唯一为 0062，三行按同一规范重建且全部仍停用。
    # head 输出会附带历史分支标签（如 ``0062 (mvp) (head)``），只断言修订号与唯一性。
    heads = _alembic_output(migration_database, "heads").splitlines()
    assert len(heads) == 1
    assert heads[0].split()[0] == "0062"
    assert "(head)" in heads[0]
    current = _alembic_output(migration_database, "current").splitlines()
    assert len(current) == 1
    assert current[0].split()[0] == "0062"
    for code in CODES:
        assert after[code] is not None
        assert markers[code] == {"revision": MIGRATION_MARKER, "was_inserted": True}
        assert after[code]["enabled"] is False
        assert after[code]["source_type"] == "market_data"
        assert after[code]["endpoint_url"] == ENDPOINTS[code]
        assert before[code] is not None
        assert after[code]["schedule"] == before[code]["schedule"]
