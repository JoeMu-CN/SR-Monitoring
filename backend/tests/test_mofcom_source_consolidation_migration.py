"""迁移 0050（商务部重复信源整合）定向验证。

沿用 tests/test_signal_validity_migration.py 的既有模式：在隔离测试库内跑真实
alembic upgrade/downgrade，绝不连接或写入当前业务库。隔离库、Alembic 运行与
seed/query 公共支持见 ``mofcom_migration_support``。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from mofcom_migration_support import (
    CONTROL_CODE,
    CONTROL_ENDPOINT_URL,
    CONTROL_PUBLISHED_ADAPTER_CONFIG,
    DETAIL_CODE,
    DETAIL_ENDPOINT_URL,
    DETAIL_SEED,
    STALE_RUN_RECOVERY_ERROR,
    insert_source,
    isolated_database,
    run_alembic,
    run_row,
    seed_business_state,
    source_row,
)
from sqlalchemy import create_engine, text


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def test_fresh_migration_chain_creates_and_neutralizes_both_sources(
    migration_database: str,
) -> None:
    # Given / When: 空库 fresh 链一路升级到 head。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        control = source_row(connection, CONTROL_CODE)
        detail = source_row(connection, DETAIL_CODE)
    engine.dispose()

    # Then: control 行保留但停用归空；detail 按 seed 约定存在且为唯一 builtin 入口。
    assert control["enabled"] is False
    assert control["schedule"] is None
    assert control["adapter_status"] == "unconfigured"
    assert control["adapter_config"] == {}
    assert detail["adapter_status"] == "builtin"
    assert detail["endpoint_url"] == DETAIL_ENDPOINT_URL
    assert detail["adapter_config"] == {}
    # 0013 以 enabled=False 播种 control，detail 继承同一停用值，fresh 链不误触发采集。
    assert detail["enabled"] is False
    assert detail["schedule"] == "0 3 * * *"


def test_upgrade_consolidates_business_state_and_finalizes_stale_runs(
    migration_database: str,
) -> None:
    # Given: 0049 上模拟业务库两源并存且各有过期 running。
    run_alembic(migration_database, "upgrade", "0049")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        ids = seed_business_state(connection)

    # When
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        control = source_row(connection, CONTROL_CODE)
        detail = source_row(connection, DETAIL_CODE)
        control_stale = run_row(connection, ids["control_stale_run"])
        control_fresh = run_row(connection, ids["control_fresh_run"])
        control_done = run_row(connection, ids["control_done_run"])
        detail_stale = run_row(connection, ids["detail_stale_run"])
        detail_done = run_row(connection, ids["detail_done_run"])
        signal_counts = dict(
            connection.execute(
                text(
                    "SELECT source_id, count(*) FROM raw_signals "
                    "WHERE source_id IN (:c, :d) GROUP BY source_id"
                ),
                {"c": ids["control_id"], "d": ids["detail_id"]},
            ).all()
        )
        run_total = connection.scalar(
            text("SELECT count(*) FROM collection_runs WHERE source_id IN (:c, :d)"),
            {"c": ids["control_id"], "d": ids["detail_id"]},
        )
    engine.dispose()

    # Then: control 停用并取消调度、行与历史保留；旧连接器恢复所需的 published
    # 配置与官方入口原样保留，供 downgrade 后旧代码的 detail→control 依赖继续工作。
    assert control["id"] == ids["control_id"]
    assert control["enabled"] is False
    assert control["schedule"] is None
    assert control["adapter_status"] == "published"
    assert control["adapter_config"] == CONTROL_PUBLISHED_ADAPTER_CONFIG
    assert control["endpoint_url"] == CONTROL_ENDPOINT_URL
    # Then: detail 成为唯一 builtin 入口，继承 control 的有效 enabled/schedule。
    assert detail["id"] == ids["detail_id"]
    assert detail["adapter_status"] == "builtin"
    assert detail["endpoint_url"] == DETAIL_ENDPOINT_URL
    assert detail["adapter_config"] == {}
    assert detail["enabled"] is True
    assert detail["schedule"] == "0 3 * * *"
    # Then: 超过 30 分钟的 running 收尾为 failed 并写稳定原因。
    for stale in (control_stale, detail_stale):
        assert stale["status"] == "failed"
        assert stale["finished_at"] is not None
        assert stale["error"] == STALE_RUN_RECOVERY_ERROR
    # Then: 新鲜 running 与终态运行不被改动。
    assert control_fresh["status"] == "running"
    assert control_fresh["finished_at"] is None
    assert control_done["status"] == "succeeded"
    assert detail_done["status"] == "succeeded"
    # Then: 历史信号与运行行全部保留。
    assert signal_counts == {ids["control_id"]: 1, ids["detail_id"]: 1}
    assert run_total == 5


def test_upgrade_without_control_keeps_detail_safe(migration_database: str) -> None:
    # Given: 0049 上 control 缺失、detail 已存在并自带配置。
    run_alembic(migration_database, "upgrade", "0049")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM data_sources WHERE code = :code"), {"code": CONTROL_CODE}
        )
        insert_source(connection, DETAIL_SEED)

    # When
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        detail = source_row(connection, DETAIL_CODE)
        control_count = connection.scalar(
            text("SELECT count(*) FROM data_sources WHERE code = :code"),
            {"code": CONTROL_CODE},
        )
    engine.dispose()

    # Then: 不重建 control，也不丢失 detail 自身 enabled/schedule。
    assert control_count == 0
    assert detail["adapter_status"] == "builtin"
    assert detail["endpoint_url"] == DETAIL_ENDPOINT_URL
    assert detail["adapter_config"] == {}
    assert detail["enabled"] is True
    assert detail["schedule"] == "0 */12 * * *"


def test_upgrade_with_both_sources_missing_creates_disabled_detail(
    migration_database: str,
) -> None:
    # Given: 0049 上两源都不存在。
    run_alembic(migration_database, "upgrade", "0049")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM data_sources WHERE code = :code"), {"code": CONTROL_CODE}
        )

    # When
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        detail = source_row(connection, DETAIL_CODE)
        control_count = connection.scalar(
            text("SELECT count(*) FROM data_sources WHERE code = :code"),
            {"code": CONTROL_CODE},
        )
    engine.dispose()

    # Then: detail 以安全停用默认值建立，不误触发采集；不凭空重建 control。
    assert control_count == 0
    assert detail["adapter_status"] == "builtin"
    assert detail["endpoint_url"] == DETAIL_ENDPOINT_URL
    assert detail["adapter_config"] == {}
    assert detail["enabled"] is False
    assert detail["schedule"] is None


def test_downgrade_restores_safe_config_without_reopening_runs(
    migration_database: str,
) -> None:
    # Given: 业务态升级到 head 后已收尾过期运行。
    run_alembic(migration_database, "upgrade", "0049")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        ids = seed_business_state(connection)
    run_alembic(migration_database, "upgrade", "head")

    # When
    run_alembic(migration_database, "downgrade", "0049")
    with engine.connect() as connection:
        control = source_row(connection, CONTROL_CODE)
        detail = source_row(connection, DETAIL_CODE)
        stale = run_row(connection, ids["control_stale_run"])

    # Then: 恢复 control 的 enabled/schedule；upgrade 保留的 published 配置与官方
    # 入口仍在，旧代码从 control 配置抓列表的 detail→control 依赖可工作；不重开
    # 已收尾运行，不删行。
    assert control["enabled"] is True
    assert control["schedule"] == "0 3 * * *"
    assert control["adapter_status"] == "published"
    assert control["adapter_config"] == CONTROL_PUBLISHED_ADAPTER_CONFIG
    assert control["endpoint_url"] == CONTROL_ENDPOINT_URL
    assert detail["id"] == ids["detail_id"]
    assert stale["status"] == "failed"
    assert stale["error"] == STALE_RUN_RECOVERY_ERROR

    # Then: 再次升级保持一致的收敛结果。
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        control_again = source_row(connection, CONTROL_CODE)
        detail_again = source_row(connection, DETAIL_CODE)
    engine.dispose()
    assert control_again["enabled"] is False
    assert control_again["schedule"] is None
    assert control_again["adapter_status"] == "published"
    assert control_again["adapter_config"] == CONTROL_PUBLISHED_ADAPTER_CONFIG
    assert detail_again["adapter_status"] == "builtin"
    assert detail_again["enabled"] is True
    assert detail_again["schedule"] == "0 3 * * *"
