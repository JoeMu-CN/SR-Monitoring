"""迁移 0060（稳定去重身份 dedup_key）定向验证。

沿用 tests/mofcom_source_consolidation_migration.py 的隔离库模式：在隔离测试库内
跑真实 alembic upgrade/downgrade，绝不连接或写入当前业务库。
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from dedup_key_migration_support import (
    MANUAL_CODE,
    MOFCOM_CODE,
    PREVIOUS_REVISION,
    USGS_CODE,
    duplicate_key_groups,
    ensure_manual_source,
    insert_signal,
    isolated_database,
    null_key_count,
    orphan_reference_count,
    run_alembic,
    signal_keys,
    source_id,
)
from sqlalchemy import create_engine, text

from app.signals.identity import compute_dedup_key


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def test_backfill_matches_runtime_dedup_key(migration_database: str) -> None:
    # Given: 0059 上同一条商务部实体信号被 HTTP/HTTPS 两种写法各写了一次，
    # 以及一条 USGS 标题被官方修订后的重复行。
    run_alembic(migration_database, "upgrade", PREVIOUS_REVISION)
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        mofcom = source_id(connection, MOFCOM_CODE)
        usgs = source_id(connection, USGS_CODE)
        http_id = insert_signal(
            connection,
            source=mofcom,
            external_id="mofcom-entity-laffitte",
            title="拉法特集团",
            url="http://aqygzj.mofcom.gov.cn/article/2026/30",
            fingerprint="fp-http",
        )
        https_id = insert_signal(
            connection,
            source=mofcom,
            external_id="mofcom-entity-laffitte",
            title="拉法特集团",
            url="https://aqygzj.mofcom.gov.cn/article/2026/30/",
            fingerprint="fp-https",
        )
        usgs_id = insert_signal(
            connection,
            source=usgs,
            external_id="us7000thzz",
            title="M 5.2 - Fiji Region (revised)",
            fingerprint="fp-usgs",
        )

    # When
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        keys = dict(signal_keys(connection, mofcom))
        usgs_key = dict(signal_keys(connection, usgs))[usgs_id]

    # Then: 键与运行时 compute_dedup_key 逐字一致；HTTP/HTTPS 两行收敛到同一个键，
    # 权威时间与采集时间相同（同一事务 now()），故 id 较大者保留原键。
    expected = compute_dedup_key(
        MOFCOM_CODE,
        external_id="mofcom-entity-laffitte",
        url="http://aqygzj.mofcom.gov.cn/article/2026/30",
        title="拉法特集团",
    )
    engine.dispose()
    assert keys[https_id] == expected
    assert keys[http_id] == f"{expected}#legacy-{http_id}"
    assert usgs_key == compute_dedup_key(
        USGS_CODE, external_id="us7000thzz", url=None, title="M 5.2 - Fiji Region (revised)"
    )


def test_collision_keeps_best_row_and_never_deletes(migration_database: str) -> None:
    # Given: 三行同键：active 且权威时间最新者应保留原键，另外两行加后缀。
    run_alembic(migration_database, "upgrade", PREVIOUS_REVISION)
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        usgs = source_id(connection, USGS_CODE)
        pending_old = insert_signal(
            connection,
            source=usgs,
            external_id="us7000thzz",
            title="旧标题",
            fingerprint="fp-1",
        )
        pending_new = insert_signal(
            connection,
            source=usgs,
            external_id="us7000thzz",
            title="新标题",
            fingerprint="fp-2",
        )
        active = insert_signal(
            connection,
            source=usgs,
            external_id="us7000thzz",
            title="官方标题",
            fingerprint="fp-3",
            validity_state="active",
            valid_from="2026-09-01T00:00:00+00:00",
        )
        manual = ensure_manual_source(connection)
        manual_kept = insert_signal(
            connection,
            source=manual,
            external_id="MANUAL-1",
            title="手工信号甲",
            fingerprint="whole-model-fp-1",
        )
        manual_legacy = insert_signal(
            connection,
            source=manual,
            external_id=None,
            title="手工信号乙",
            fingerprint="whole-model-fp-2",
        )
        connection.execute(
            text(
                "INSERT INTO ai_analysis_records (signal_id, provider, model, "
                "prompt_version, status, started_at, finished_at, duration_ms) VALUES "
                "(:signal_id, 'fake', 'fake', 'signal-analysis-v6', 'succeeded', "
                "now(), now(), 1)"
            ),
            {"signal_id": pending_old},
        )

    # When
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        usgs_keys = dict(signal_keys(connection, usgs))
        manual_keys = dict(signal_keys(connection, manual))
        total = connection.scalar(text("SELECT count(*) FROM raw_signals"))
        nulls = null_key_count(connection)
        duplicates = duplicate_key_groups(connection)
        orphans = orphan_reference_count(connection)
        analysis_rows = connection.scalar(
            text("SELECT count(*) FROM ai_analysis_records WHERE signal_id = :id"),
            {"id": pending_old},
        )
    engine.dispose()

    # Then: 一行无历史行被删除，AI 分析记录仍完整挂在原信号上。
    assert total == 5
    assert analysis_rows == 1
    assert orphans == 0
    assert nulls == 0
    assert duplicates == 0

    # Then: active 行保留原键，其余两行追加 #legacy-{id}，没有任何行被删除。
    expected = compute_dedup_key(
        USGS_CODE, external_id="us7000thzz", url=None, title="官方标题"
    )
    assert usgs_keys[active] == expected
    assert usgs_keys[pending_old] == f"{expected}#legacy-{pending_old}"
    assert usgs_keys[pending_new] == f"{expected}#legacy-{pending_new}"

    # Then: manual-json 有 external_id 走 external_id 身份；无 external_id 的历史行
    # 沿用其整模型指纹（与运行时 ManualJsonAdapter.dedup_key 的回落分支一致）。
    assert manual_keys[manual_kept] == compute_dedup_key(
        MANUAL_CODE,
        external_id="MANUAL-1",
        url=None,
        title="手工信号甲",
        fields=("external_id",),
    )
    assert manual_keys[manual_legacy] == "whole-model-fp-2"


def test_upgrade_is_idempotent_on_backfilled_data(migration_database: str) -> None:
    # Given: 已升级到 head 的库。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        usgs = source_id(connection, USGS_CODE)
        first = insert_signal(
            connection,
            source=usgs,
            external_id="us7000abcd",
            title="标题",
            fingerprint="fp-a",
            dedup_key="seed-a",
        )
        second = insert_signal(
            connection,
            source=usgs,
            external_id="us7000abcd",
            title="标题",
            fingerprint="fp-b",
            dedup_key="seed-b",
        )

    # When / Then: 降级再升级得到同一收敛结果（后缀按 id 稳定，不漂移）。
    run_alembic(migration_database, "downgrade", PREVIOUS_REVISION)
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        keys = dict(signal_keys(connection, usgs))
        nulls = null_key_count(connection)
        duplicates = duplicate_key_groups(connection)
    engine.dispose()

    expected = compute_dedup_key(USGS_CODE, external_id="us7000abcd", url=None, title="标题")
    # 碰撞消解按（active → valid_from → collected_at → id DESC）保留最优行：两行同键
    # 且权威时间相同，id 较大者保留原键，另一行稳定追加 #legacy 后缀。
    assert keys[second] == expected
    assert keys[first] == f"{expected}#legacy-{first}"
    assert nulls == 0
    assert duplicates == 0


def test_downgrade_drops_column_and_constraint(migration_database: str) -> None:
    # Given
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        insert_signal(
            connection,
            source=source_id(connection, USGS_CODE),
            external_id="us7000wxyz",
            title="标题",
            fingerprint="fp",
            dedup_key="seed",
        )

    # When
    run_alembic(migration_database, "downgrade", PREVIOUS_REVISION)
    with engine.connect() as connection:
        columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'raw_signals'"
                )
            ).all()
        }
        total = connection.scalar(text("SELECT count(*) FROM raw_signals"))
    engine.dispose()

    # Then: 列与唯一约束消失，历史行原样保留（降级无损）。
    assert "dedup_key" not in columns
    assert total == 1