"""迁移 0066（信号处理状态拆分）定向验证。

沿用 ``tests/test_signal_irrelevant_migration.py`` 的隔离库模式：在隔离测试库内跑真实
alembic upgrade/downgrade，绝不连接或写入当前业务库。

契约：
- upgrade：新增 ``processing_state``（5 值 CHECK，NOT NULL DEFAULT
  'pending_classification'）；``validity_state`` 放开 NOT NULL 并去掉旧默认值（NULL
  才代表「生命周期未定」）；3 条既有 CHECK 显式放行 NULL；同一事务内按 a→e 顺序回填
  （待分类置空 → irrelevant 推断 → 事件关联 analyzed → market-eval no_trigger →
  分类失败）。
- downgrade：filtered/no_trigger 回到 irrelevant，生命周期未定的行回到
  pending_classification，恢复 0059 的 3 条 CHECK 与 NOT NULL 默认值并删列。
"""

from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from mofcom_migration_support import (
    SourceSeed,
    insert_source,
    isolated_database,
    run_alembic,
)
from sqlalchemy import Connection, create_engine, text

SOURCE_CODE = "processing-state-migration-source"
PENDING_REASON: dict[str, object] = {
    "code": "pending_classification",
    "anchor_source": "collected_at",
    "details": {},
}
FILTERED_REASON: dict[str, object] = {
    "code": "filtered_out",
    "anchor_source": "collected_at",
    "details": {"filter": "relevance", "reason": "与业务无关"},
}
MARKET_REASON: dict[str, object] = {
    "code": "filtered_out",
    "anchor_source": "collected_at",
    "details": {"filter": "market_indicator", "reason": "未达触发线"},
}
FAILED_REASON: dict[str, object] = {
    "code": "classification_failed",
    "anchor_source": "collected_at",
    "details": {"analysis_id": 1, "reason": "event_subtype_missing"},
}
POLICY_REASON: dict[str, object] = {
    "code": "policy_resolved",
    "anchor_source": "collected_at",
    "details": {},
}

INSERT_SIGNAL_SQL = text(
    """
    INSERT INTO raw_signals (
        source_id, title, content, fingerprint, dedup_key, raw_data,
        collected_at, validity_state, validity_reason
    ) VALUES (
        :source_id, :fingerprint, 'content', :fingerprint, :fingerprint, '{}'::jsonb,
        now(), :state, CAST(:reason AS jsonb)
    ) RETURNING id
    """
)
INSERT_ACTIVE_SIGNAL_SQL = text(
    """
    INSERT INTO raw_signals (
        source_id, title, content, fingerprint, dedup_key, raw_data,
        collected_at, validity_state, validity_reason, validity_profile,
        validity_mode, valid_from, valid_until, validity_policy_version
    ) VALUES (
        :source_id, :fingerprint, 'content', :fingerprint, :fingerprint, '{}'::jsonb,
        now(), 'active', CAST(:reason AS jsonb), 'weather_alert', 'fixed_days',
        now() - interval '1 day', now() + interval '2 day', 'policy-v1'
    ) RETURNING id
    """
)
INSERT_MARKET_ANALYSIS_SQL = text(
    """
    INSERT INTO ai_analysis_records (
        signal_id, provider, model, prompt_version, status, finished_at, result
    ) VALUES (
        :signal_id, 'deterministic-market', 'market-eval-v1', 'market-eval-v1',
        'succeeded', now(), NULL
    )
    """
)


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def _seed_source(connection: Connection) -> int:
    return insert_source(
        connection,
        SourceSeed(
            code=SOURCE_CODE, schedule="0 6 * * *", enabled=True, adapter_status="published"
        ),
    )


def _insert_signal(
    connection: Connection,
    source_id: int,
    fingerprint: str,
    *,
    state: str,
    reason: dict[str, object],
) -> int:
    return connection.execute(
        INSERT_SIGNAL_SQL,
        {
            "source_id": source_id,
            "fingerprint": fingerprint,
            "state": state,
            "reason": json.dumps(reason),
        },
    ).scalar_one()


def _insert_active_signal(
    connection: Connection, source_id: int, fingerprint: str
) -> int:
    return connection.execute(
        INSERT_ACTIVE_SIGNAL_SQL,
        {
            "source_id": source_id,
            "fingerprint": fingerprint,
            "reason": json.dumps(POLICY_REASON),
        },
    ).scalar_one()


def _link_event(connection: Connection, signal_id: int, dedup_key: str) -> None:
    event_id = connection.execute(
        text(
            "INSERT INTO risk_events (dedup_key, event_type, severity, summary, "
            "confidence, facts, validity_state, validity_reason) VALUES "
            "(:dedup_key, 'weather', 'high', '迁移用例事件', 0.9, '{}'::jsonb, "
            "'active', CAST(:reason AS jsonb)) RETURNING id"
        ),
        {"dedup_key": dedup_key, "reason": json.dumps(POLICY_REASON)},
    ).scalar_one()
    connection.execute(
        text(
            "INSERT INTO risk_event_signals (event_id, signal_id) "
            "VALUES (:event_id, :signal_id)"
        ),
        {"event_id": event_id, "signal_id": signal_id},
    )


def _seed_history(connection: Connection) -> dict[str, int]:
    """模拟 0065 上的历史形态：五种待回填 + 两类对照信号。"""
    source_id = _seed_source(connection)
    ids = {
        "pending": _insert_signal(
            connection, source_id, "pending", state="pending_classification", reason=PENDING_REASON
        ),
        "filtered": _insert_signal(
            connection, source_id, "filtered", state="irrelevant", reason=FILTERED_REASON
        ),
        "market": _insert_signal(
            connection, source_id, "market", state="irrelevant", reason=MARKET_REASON
        ),
        "failed": _insert_signal(
            connection,
            source_id,
            "failed",
            state="pending_classification",
            reason=FAILED_REASON,
        ),
        "event": _insert_active_signal(connection, source_id, "event"),
        "market-eval": _insert_active_signal(connection, source_id, "market-eval"),
        "active": _insert_active_signal(connection, source_id, "active"),
    }
    _link_event(connection, ids["event"], "processing-state-migration-event")
    connection.execute(INSERT_MARKET_ANALYSIS_SQL, {"signal_id": ids["market-eval"]})
    return ids


def _states(connection: Connection) -> dict[str, tuple[object, object]]:
    rows = connection.execute(
        text(
            "SELECT fingerprint, validity_state, processing_state "
            "FROM raw_signals ORDER BY fingerprint"
        )
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_upgrade_backfills_processing_state_and_clears_unknown_lifecycle(
    migration_database: str,
) -> None:
    # Given：0065 上的五种历史形态。
    run_alembic(migration_database, "upgrade", "0065")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)

    # When
    run_alembic(migration_database, "upgrade", "0066")
    with engine.connect() as connection:
        states = _states(connection)
        columns = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                text(
                    "SELECT column_name, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_name = 'raw_signals' "
                    "AND column_name IN ('processing_state','validity_state')"
                )
            ).all()
        }
        filters = connection.execute(
            text(
                "SELECT count(*) FROM raw_signals "
                "WHERE validity_state IS NULL AND validity_reason->>'code' = 'filtered_out'"
            )
        ).scalar_one()
    engine.dispose()

    # Then：待分类的生命周期置空；irrelevant 按 filter 推断阶段并尽量还原生命周期。
    assert states["pending"] == (None, "pending_classification")
    assert states["filtered"] == (None, "filtered")
    assert states["market"] == (None, "no_trigger")
    # 事件关联优先于 irrelevant 推断；行情评估记录判为未触发（生命周期保持 active）。
    assert states["event"] == ("active", "analyzed")
    assert states["market-eval"] == ("active", "no_trigger")
    # 分类失败只改处理阶段；未触碰的 active 仍是待处理阶段。
    assert states["failed"] == (None, "classification_failed")
    assert states["active"] == ("active", "pending_classification")
    # 过滤原因不被回填改写。
    assert filters == 2

    # Then：validity_state 放开 NOT NULL 且去掉旧默认值；processing_state 无默认缺失。
    assert columns["validity_state"] == ("YES", None)
    assert columns["processing_state"] == ("NO", "'pending_classification'::text")


def test_downgrade_restores_legacy_semantics_and_drops_column(
    migration_database: str,
) -> None:
    # Given：0066 回填后的数据。
    run_alembic(migration_database, "upgrade", "0065")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)
    run_alembic(migration_database, "upgrade", "0066")

    # When
    run_alembic(migration_database, "downgrade", "0065")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT fingerprint, validity_state FROM raw_signals "
                "ORDER BY fingerprint"
            )
        ).all()
        has_column = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'raw_signals' AND column_name = 'processing_state'"
            )
        ).scalar_one()
        validity_nullable = connection.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'raw_signals' AND column_name = 'validity_state'"
            )
        ).one()
    engine.dispose()

    # Then：filtered/no_trigger 回到 irrelevant，未定生命周期回到 pending_classification。
    states = dict(rows)
    assert states["pending"] == "pending_classification"
    assert states["failed"] == "pending_classification"
    assert states["filtered"] == "irrelevant"
    assert states["market"] == "irrelevant"
    # 旧 schema 无法表达「active + 未触发」，行情评估记录一并回到 irrelevant。
    assert states["market-eval"] == "irrelevant"
    assert states["event"] == "active"
    assert states["active"] == "active"
    # Then：列与默认值按 0059 形态恢复。
    assert has_column == 0
    assert validity_nullable.is_nullable == "NO"
    assert validity_nullable.column_default == "'pending_classification'::text"


def test_round_trip_upgrade_downgrade_upgrade_keeps_stages(
    migration_database: str,
) -> None:
    # Given：完整往返一次后的库。
    run_alembic(migration_database, "upgrade", "0065")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)
    run_alembic(migration_database, "upgrade", "0066")
    run_alembic(migration_database, "downgrade", "0065")

    # When：再次升级（irrelevant 行按 filter 重新推断阶段）。
    run_alembic(migration_database, "upgrade", "0066")
    with engine.connect() as connection:
        states = _states(connection)
    engine.dispose()

    # Then：阶段判定稳定，不漂移。
    assert states["pending"] == (None, "pending_classification")
    assert states["filtered"] == (None, "filtered")
    assert states["market"] == (None, "no_trigger")
    assert states["event"] == ("active", "analyzed")
