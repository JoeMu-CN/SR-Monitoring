"""迁移 0059（信号显式终态 irrelevant）定向验证。

沿用 tests/test_source_validity_review_removal_migration.py 的隔离库模式：在隔离测试库内
跑真实 alembic upgrade/downgrade 往返，绝不连接或写入当前业务库。

契约：
- upgrade（顺序固定）：drop 3 条旧 CHECK → create 3 条新 CHECK（validity_state 白名单
  加入 irrelevant；policy_snapshot / deadline_mode 的无快照豁免集扩为
  ('pending_classification','irrelevant','legacy')）→ 单条幂等 UPDATE 回填。
- 回填谓词与 ``_terminal_filtered_signal_ids()`` 的空结果判定逐字一致：只有
  ``status='succeeded'`` 且 ``result`` 为 SQL NULL 或 JSON null 的记录算终态过滤；
  ``classification_failed`` 的信号保持 pending 供人工复核；active 与无分析记录的
  pending 信号不被触碰。
- downgrade：守卫拒绝未知 reason code 的 irrelevant 行；否则 drop 新 CHECK → 改回
  pending_classification + 0047 的 PENDING_REASON → 重建 0047 原文 3 条 CHECK。
- models.py 的 3 条约束文本必须与迁移目标态文本逐字一致。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Generator
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
from sqlalchemy.exc import IntegrityError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = BACKEND_ROOT / "alembic" / "versions" / "0059_signal_irrelevant_state.py"
SOURCE_CODE = "irrelevant-migration-source"
FILTERED_OUT_REASON: dict[str, object] = {
    "code": "filtered_out",
    "anchor_source": "collected_at",
    "details": {"filter": "prefilter", "reason": "历史分析成功但结果为空"},
}
PENDING_REASON: dict[str, object] = {
    "code": "pending_classification",
    "anchor_source": "collected_at",
    "details": {},
}
CLASSIFICATION_FAILED_REASON: dict[str, object] = {
    "code": "classification_failed",
    "anchor_source": "collected_at",
    "details": {"analysis_id": 1, "reason": "event_subtype_missing"},
}

# 空结果的三种落库形态：SQL NULL、JSON null（ORM result=None 的真实形态）、真实对象。
RESULT_SQL_NULL = "NULL"
RESULT_JSON_NULL = "'null'::jsonb"
RESULT_OBJECT = """'{"event_subtype": "weather_alert"}'::jsonb"""

INSERT_SIGNAL_SQL = text(
    "INSERT INTO raw_signals (source_id, title, content, fingerprint, raw_data, "
    "published_at, validity_state, validity_reason) VALUES (:source_id, :title, "
    "'content', :fingerprint, '{}'::jsonb, :published_at, :state, "
    "CAST(:reason AS jsonb)) RETURNING id"
)
INSERT_ACTIVE_SIGNAL_SQL = text(
    "INSERT INTO raw_signals (source_id, title, content, fingerprint, raw_data, "
    "published_at, validity_state, validity_reason, validity_profile, validity_mode, "
    "valid_from, valid_until, validity_policy_version) VALUES (:source_id, :title, "
    "'content', :fingerprint, '{}'::jsonb, '2026-09-01 00:00:00+00', 'active', "
    "CAST(:reason AS jsonb), 'weather_alert', 'fixed_days', '2026-09-01 00:00:00+00', "
    "'2026-09-04 00:00:00+00', 'policy-v1') RETURNING id"
)
INSERT_IRRELEVANT_SQL = text(
    "INSERT INTO raw_signals (source_id, title, content, fingerprint, raw_data, "
    "validity_state, validity_reason) VALUES (:source_id, 'irrelevant', 'content', "
    "'irrelevant-empty', '{}'::jsonb, 'irrelevant', CAST(:reason AS jsonb))"
)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_module("migration_0059", MIGRATION_FILE)
TARGET_CHECKS: dict[str, str] = dict(_MIGRATION.TARGET_CHECKS)
LEGACY_CHECKS: dict[str, str] = dict(_MIGRATION.LEGACY_CHECKS)


def _normalize(sql_text: str) -> str:
    return " ".join(sql_text.split())


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


def _seed_signal(
    connection: Connection,
    source_id: int,
    fingerprint: str,
    *,
    reason: dict[str, object] = PENDING_REASON,
    published_at: str | None = "2026-09-01 00:00:00+00",
) -> int:
    return connection.execute(
        INSERT_SIGNAL_SQL,
        {
            "source_id": source_id,
            "title": fingerprint,
            "fingerprint": fingerprint,
            "published_at": published_at,
            "state": "pending_classification",
            "reason": json.dumps(reason),
        },
    ).scalar_one()


def _seed_filtered_analysis(
    connection: Connection, signal_id: int, *, result_sql: str
) -> None:
    """写入一条确定性预筛形态的成功分析记录；result_sql 为本文件定义的 SQL 字面量。"""
    connection.execute(
        text(
            "INSERT INTO ai_analysis_records (signal_id, provider, model, prompt_version, "
            "status, finished_at, result) VALUES (:signal_id, 'deterministic-filter', "
            "'relevance-v1', 'relevance-v1', 'succeeded', now(), " + result_sql + ")"
        ),
        {"signal_id": signal_id},
    )


def _seed_history(connection: Connection) -> int:
    """模拟 0058 上的历史形态：两种空结果 pending 信号 + 三类不得被回填触碰的对照。"""
    source_id = _seed_source(connection)
    _seed_filtered_analysis(
        connection, _seed_signal(connection, source_id, "json-null"), result_sql=RESULT_JSON_NULL
    )
    _seed_filtered_analysis(
        connection,
        _seed_signal(connection, source_id, "sql-null", published_at=None),
        result_sql=RESULT_SQL_NULL,
    )
    _seed_filtered_analysis(
        connection, _seed_signal(connection, source_id, "object"), result_sql=RESULT_OBJECT
    )
    _seed_filtered_analysis(
        connection,
        _seed_signal(
            connection, source_id, "classification-failed",
            reason=CLASSIFICATION_FAILED_REASON,
        ),
        result_sql=RESULT_JSON_NULL,
    )
    active_signal_id = connection.execute(
        INSERT_ACTIVE_SIGNAL_SQL,
        {
            "source_id": source_id,
            "title": "active-empty",
            "fingerprint": "active-empty",
            "reason": json.dumps(
                {
                    "code": "classification_resolved",
                    "anchor_source": "published_at",
                    "details": {"event_subtype": "weather_alert"},
                }
            ),
        },
    ).scalar_one()
    _seed_filtered_analysis(
        connection, active_signal_id, result_sql=RESULT_JSON_NULL
    )
    _seed_signal(connection, source_id, "pending-no-analysis")
    return source_id


def _signal_row(connection: Connection, fingerprint: str) -> dict[str, object]:
    row = connection.execute(
        text(
            "SELECT validity_state, validity_reason FROM raw_signals "
            "WHERE fingerprint = :fingerprint"
        ),
        {"fingerprint": fingerprint},
    ).mappings().one()
    return dict(row)


def test_upgrade_backfills_only_empty_result_pending_signals(
    migration_database: str,
) -> None:
    # Given：0058 上 pending 长期滞留的历史信号（两种空结果形态 + 三类对照信号）。
    run_alembic(migration_database, "upgrade", "0058")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)

    # When：升级到 0059。
    run_alembic(migration_database, "upgrade", "0059")
    with engine.connect() as connection:
        json_null = _signal_row(connection, "json-null")
        sql_null = _signal_row(connection, "sql-null")
        object_result = _signal_row(connection, "object")
        classification_failed = _signal_row(connection, "classification-failed")
        active_empty = _signal_row(connection, "active-empty")
        pending_no_analysis = _signal_row(connection, "pending-no-analysis")
    engine.dispose()

    # Then：JSON null 与 SQL NULL 两种空结果 pending 信号进入 irrelevant 终态。
    assert json_null["validity_state"] == "irrelevant"
    assert json_null["validity_reason"]["code"] == "filtered_out"
    assert json_null["validity_reason"]["anchor_source"] == "published_at"
    assert json_null["validity_reason"]["details"]["filter"] == "prefilter"
    assert json_null["validity_reason"]["details"]["reason"]
    assert sql_null["validity_state"] == "irrelevant"
    assert sql_null["validity_reason"]["anchor_source"] == "collected_at"
    # Then：真实结果、classification_failed、active 与无分析记录的信号完全不动。
    assert object_result == {
        "validity_state": "pending_classification",
        "validity_reason": PENDING_REASON,
    }
    assert classification_failed == {
        "validity_state": "pending_classification",
        "validity_reason": CLASSIFICATION_FAILED_REASON,
    }
    assert active_empty["validity_state"] == "active"
    assert pending_no_analysis == {
        "validity_state": "pending_classification",
        "validity_reason": PENDING_REASON,
    }


def test_repeated_backfill_is_noop(migration_database: str) -> None:
    # Given：0059 已完成一次回填。
    run_alembic(migration_database, "upgrade", "0058")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)
    run_alembic(migration_database, "upgrade", "0059")

    # When：重放同一条回填语句（模拟迁移重试或重复执行）。
    with engine.begin() as connection:
        changed = connection.execute(text(_MIGRATION.BACKFILL_UPDATE)).rowcount
    engine.dispose()

    # Then：无匹配行，天然幂等。
    assert changed == 0


def test_downgrade_restores_pending_classification_and_legacy_checks(
    migration_database: str,
) -> None:
    # Given：0059 回填后的 irrelevant 行。
    run_alembic(migration_database, "upgrade", "0058")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)
    run_alembic(migration_database, "upgrade", "0059")

    # When：降级到 0058。
    run_alembic(migration_database, "downgrade", "0058")
    with engine.connect() as connection:
        json_null = _signal_row(connection, "json-null")
        sql_null = _signal_row(connection, "sql-null")
    engine.dispose()

    # Then：回到 pending_classification 与 0047 的 PENDING_REASON。
    assert json_null == {
        "validity_state": "pending_classification",
        "validity_reason": PENDING_REASON,
    }
    assert sql_null == {
        "validity_state": "pending_classification",
        "validity_reason": PENDING_REASON,
    }

    # Then：0047 原文语义全部恢复（每条写入各自独立事务，失败即回滚）。
    rejected_after_downgrade = (
        # validity_state 白名单回到 0047 原文：irrelevant 不可写入。
        "UPDATE raw_signals SET validity_state = 'irrelevant' WHERE fingerprint = 'object'",
        # policy_snapshot 豁免回到 ('pending_classification','legacy')。
        "UPDATE raw_signals SET validity_profile = NULL, validity_mode = NULL, "
        "valid_from = NULL, validity_policy_version = NULL "
        "WHERE fingerprint = 'active-empty'",
        # deadline_mode 豁免同步回到：active + fixed_days 必须带 valid_until。
        "UPDATE raw_signals SET valid_until = NULL WHERE fingerprint = 'active-empty'",
    )
    for statement in rejected_after_downgrade:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement))
    engine.dispose()


def test_downgrade_guard_rejects_unknown_reason_code(migration_database: str) -> None:
    # Given：irrelevant 行被未知生产者写成别的 reason code。
    run_alembic(migration_database, "upgrade", "0058")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)
    run_alembic(migration_database, "upgrade", "0059")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE raw_signals SET validity_reason = CAST("
                "'{\"code\":\"foreign_producer\",\"anchor_source\":\"legacy\","
                "\"details\":{}}' AS jsonb) WHERE fingerprint = 'json-null'"
            )
        )

    # When / Then：降级被守卫拒绝，不静默丢弃未知状态语义。
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run_alembic(migration_database, "downgrade", "0058")

    assert "cannot downgrade 0059" in caught.value.stderr
    engine.dispose()


def test_irrelevant_signal_needs_no_policy_snapshot(migration_database: str) -> None:
    # Given：0058 旧约束下，策略快照全 NULL 的行只能落 pending_classification。
    run_alembic(migration_database, "upgrade", "0058")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        source_id = _seed_source(connection)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            INSERT_IRRELEVANT_SQL,
            {"source_id": source_id, "reason": json.dumps(FILTERED_OUT_REASON)},
        )

    # When：升级到 0059 后写入同一行。
    run_alembic(migration_database, "upgrade", "0059")
    with engine.begin() as connection:
        connection.execute(
            INSERT_IRRELEVANT_SQL,
            {"source_id": source_id, "reason": json.dumps(FILTERED_OUT_REASON)},
        )
        state = connection.scalar(
            text(
                "SELECT validity_state FROM raw_signals "
                "WHERE fingerprint = 'irrelevant-empty'"
            )
        )
    engine.dispose()

    # Then：无策略快照、无截止时间的 irrelevant 合法（豁免集生效）。
    assert state == "irrelevant"


def test_round_trip_upgrade_downgrade_upgrade_keeps_states(
    migration_database: str,
) -> None:
    # Given：0059 回填后的状态。
    run_alembic(migration_database, "upgrade", "0058")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_history(connection)
    run_alembic(migration_database, "upgrade", "0059")

    # When：downgrade → upgrade 往返。
    run_alembic(migration_database, "downgrade", "0058")
    run_alembic(migration_database, "upgrade", "0059")
    with engine.connect() as connection:
        json_null = _signal_row(connection, "json-null")
        sql_null = _signal_row(connection, "sql-null")
        classification_failed = _signal_row(connection, "classification-failed")
        object_result = _signal_row(connection, "object")
    engine.dispose()

    # Then：两轮后判定稳定，不漂移、不误伤。
    assert json_null["validity_state"] == "irrelevant"
    assert sql_null["validity_state"] == "irrelevant"
    assert classification_failed["validity_state"] == "pending_classification"
    assert object_result["validity_state"] == "pending_classification"


def test_models_constraint_text_matches_migration() -> None:
    from app.signals.models import RawSignal

    constraints = {
        item.name: str(item.sqltext)
        for item in RawSignal.__table__.constraints
        if item.name in TARGET_CHECKS
    }

    assert set(constraints) == set(TARGET_CHECKS)
    for name, migration_text in TARGET_CHECKS.items():
        assert _normalize(constraints[name]) == _normalize(migration_text)


def test_migration_legacy_checks_keep_0047_exemption_sets() -> None:
    # 0047 三条原文均不含 irrelevant；0059 目标态三条都必须含，且豁免集精确为三值。
    for name, legacy_text in LEGACY_CHECKS.items():
        assert "irrelevant" not in legacy_text
        assert "irrelevant" in TARGET_CHECKS[name]
    for name in ("ck_raw_signals_policy_snapshot", "ck_raw_signals_deadline_mode"):
        assert "'pending_classification','irrelevant','legacy'" in TARGET_CHECKS[name]
        assert "validity_state IN ('pending_classification','legacy')" in LEGACY_CHECKS[name]