"""迁移 0053（天眼查 until_superseded 策略 + 周度说明）定向验证。

沿用 tests/test_signal_validity_migration.py 与
tests/test_mofcom_source_consolidation_migration.py 的隔离库模式：在隔离测试库内跑
真实 alembic upgrade/downgrade 往返，绝不连接或写入当前业务库。

契约：
- upgrade：仅当 tianyancha 行的 validity_policy 为 SQL NULL（fresh chain）或旧默认
  fixed_days/30，且 description 恰为 0051 文案时整行迁移；description 的 SQL NULL 只
  可能是运营/异常改写（0015 建表即写非 NULL、0051 又无条件重写），故视同自定义、整行
  0 行更新。迁移前把原始值（含 SQL NULL 语义）写进 login_config 的 0053 专属 marker，
  policy/description 两键始终存在；policy 键为 JSON null 表示原值为 SQL NULL。
- downgrade：仅处理带有效 0053 marker（对象、revision 明确、两个键齐全）的行；字段
  仍等于 0053 目标值才从 marker 还原，已被运营再次修改的字段保留；最后删除 marker。
  缺失/无关 marker 一律 no-op，0052 的预算 marker 不受影响。
- fresh-chain 的 SQL NULL 与旧默认状态均可精确往返。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from types import ModuleType

import pytest
from mofcom_migration_support import isolated_database, run_alembic
from sqlalchemy import Connection, create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = BACKEND_ROOT / "alembic" / "versions" / "0053_tyc_profile_validity.py"
TYC_CODE = "tianyancha"
OTHER_CODE = "ofac-sdn"
STALE_DESCRIPTION_FRAGMENT = "每天北京时间 06:00"
CUSTOM_POLICY: dict[str, object] = {"mode": "until_revoked", "review_required": False}
CUSTOM_DESCRIPTION = "运营自定义说明"


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0053", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration_module()
TARGET_POLICY: dict[str, object] = _MIGRATION.TARGET_POLICY
PREVIOUS_POLICY: dict[str, object] = _MIGRATION.PREVIOUS_POLICY
TARGET_DESCRIPTION: str = _MIGRATION.TARGET_DESCRIPTION
PREVIOUS_DESCRIPTION: str = _MIGRATION.PREVIOUS_DESCRIPTION
MIGRATION_MARKER: str = _MIGRATION.MIGRATION_MARKER
MARKER_KEY: str = _MIGRATION.MARKER_KEY


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def _source_row(connection: Connection, code: str) -> dict[str, object] | None:
    """读取策略/说明及其 SQL NULL 语义（JSON null 不算 SQL NULL）。"""
    row = connection.execute(
        text(
            "SELECT validity_policy, description, "
            "validity_policy IS NULL AS policy_is_sql_null, "
            "description IS NULL AS description_is_sql_null "
            "FROM data_sources WHERE code = :code"
        ),
        {"code": code},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def _source_count(connection: Connection, code: str) -> int:
    return int(
        connection.scalar(
            text("SELECT count(*) FROM data_sources WHERE code = :code"), {"code": code}
        )
    )


def _login_config(connection: Connection, code: str) -> dict[str, object]:
    raw = connection.scalar(
        text("SELECT login_config::text FROM data_sources WHERE code = :code"),
        {"code": code},
    )
    assert raw is not None
    return json.loads(raw)


def _marker(connection: Connection, code: str) -> dict[str, object] | None:
    """读取 0053 marker；键缺失、JSON null 或非对象时返回 None。"""
    raw = connection.scalar(
        text("SELECT (login_config->:key)::text FROM data_sources WHERE code = :code"),
        {"key": MARKER_KEY, "code": code},
    )
    if raw is None:
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None


def _set_source(
    connection: Connection,
    code: str,
    *,
    policy: dict[str, object] | None,
    description: str | None,
) -> None:
    connection.execute(
        text(
            "UPDATE data_sources SET validity_policy = CAST(:policy AS jsonb), "
            "description = :description WHERE code = :code"
        ),
        {
            "code": code,
            "policy": None if policy is None else json.dumps(policy),
            "description": description,
        },
    )


def _patch_marker(
    connection: Connection, code: str, patch: dict[str, str | None]
) -> None:
    """改写已存在的 0053 marker：值为 None 表示删除该键，否则覆盖为字符串。"""
    config = _login_config(connection, code)
    marker = config[MARKER_KEY]
    assert isinstance(marker, dict)
    for key, value in patch.items():
        if value is None:
            marker.pop(key, None)
        else:
            marker[key] = value
    connection.execute(
        text(
            "UPDATE data_sources SET login_config = CAST(:config AS jsonb) "
            "WHERE code = :code"
        ),
        {"code": code, "config": json.dumps(config, ensure_ascii=False)},
    )


def _replace_marker(connection: Connection, code: str, raw_marker: str) -> None:
    connection.execute(
        text(
            "UPDATE data_sources SET login_config = jsonb_set("
            f"login_config, '{{{MARKER_KEY}}}', CAST(:marker AS jsonb), true) "
            "WHERE code = :code"
        ),
        {"code": code, "marker": raw_marker},
    )


def _alembic_heads(database_url: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_fresh_chain_upgrade_sets_target_and_records_null_policy_marker(
    migration_database: str,
) -> None:
    # Given / When：空库 fresh 链一路升级到 head。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
        marker = _marker(connection, TYC_CODE)
        config = _login_config(connection, TYC_CODE)
    engine.dispose()

    # Then：策略精确等于目标；说明为周度分片文案且不含陈旧「每天北京时间 06:00」。
    assert row is not None
    assert row["validity_policy"] == TARGET_POLICY
    assert row["description"] == TARGET_DESCRIPTION
    assert STALE_DESCRIPTION_FRAGMENT not in str(row["description"])
    # Then：marker 记录 fresh-chain 前态——policy 键存在且为 JSON null（原值 SQL NULL），
    # 说明为 0051 文案；revision 标识明确；login_config 其它键保留。
    assert marker is not None
    assert marker["revision"] == MIGRATION_MARKER
    assert "policy" in marker
    assert marker["policy"] is None
    assert marker["description"] == PREVIOUS_DESCRIPTION
    assert config.get("tyc_budget_migration") == "0052"
    assert config.get("daily_limit") == 1000


def test_fresh_chain_downgrade_restores_sql_null_and_round_trips(
    migration_database: str,
) -> None:
    # Given：fresh 链升级到 head。
    run_alembic(migration_database, "upgrade", "head")

    # When：降级到 0052。
    run_alembic(migration_database, "downgrade", "0052")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        restored = _source_row(connection, TYC_CODE)
        marker_after = _marker(connection, TYC_CODE)
        config = _login_config(connection, TYC_CODE)

    # Then：fresh-chain 的 SQL NULL 精确还原（不是固定写回的 fixed_days/30）；说明回到
    # 0051 文案；marker 删除且 0052 预算 marker 不受影响。
    assert restored is not None
    assert restored["policy_is_sql_null"] is True
    assert restored["validity_policy"] is None
    assert restored["description"] == PREVIOUS_DESCRIPTION
    assert marker_after is None
    assert config.get("tyc_budget_migration") == "0052"

    # When：再次升级到 head；Then：回到 0053 目标态（往返一致）。
    run_alembic(migration_database, "upgrade", "head")
    with engine.connect() as connection:
        again = _source_row(connection, TYC_CODE)
    engine.dispose()
    assert again is not None and again["validity_policy"] == TARGET_POLICY
    assert again["description"] == TARGET_DESCRIPTION


def test_legacy_default_round_trips_exactly(migration_database: str) -> None:
    # Given：0052 业务默认——策略 fixed_days/30、说明 0051 文案。
    run_alembic(migration_database, "upgrade", "0052")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _set_source(
            connection,
            TYC_CODE,
            policy=PREVIOUS_POLICY,
            description=PREVIOUS_DESCRIPTION,
        )

    # When：升级到 0053；Then：切到目标态，marker 记录旧默认原始值。
    run_alembic(migration_database, "upgrade", "0053")
    with engine.connect() as connection:
        upgraded = _source_row(connection, TYC_CODE)
        marker = _marker(connection, TYC_CODE)
    assert upgraded is not None and upgraded["validity_policy"] == TARGET_POLICY
    assert marker == {
        "revision": MIGRATION_MARKER,
        "policy": PREVIOUS_POLICY,
        "description": PREVIOUS_DESCRIPTION,
    }

    # When：降级到 0052；Then：旧默认逐字段精确还原，marker 删除。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        restored = _source_row(connection, TYC_CODE)
        marker_after = _marker(connection, TYC_CODE)
    engine.dispose()
    assert restored is not None
    assert restored["validity_policy"] == PREVIOUS_POLICY
    assert restored["description"] == PREVIOUS_DESCRIPTION
    assert restored["policy_is_sql_null"] is False
    assert marker_after is None


@pytest.mark.parametrize("policy", [None, PREVIOUS_POLICY])
def test_upgrade_sql_null_description_is_custom_and_kept(
    migration_database: str, policy: dict[str, object] | None
) -> None:
    # Given：description 为 SQL NULL——正常链路不可能自然产生（0015 建表即写非 NULL、
    # 0051 又无条件重写），只能是运营/异常改写，故视为自定义前态；policy 覆盖 fresh
    # chain 的 SQL NULL 与旧默认两种已知前态。
    run_alembic(migration_database, "upgrade", "0052")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _set_source(connection, TYC_CODE, policy=policy, description=None)

    # When：升级到 0053。
    run_alembic(migration_database, "upgrade", "0053")
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
        marker = _marker(connection, TYC_CODE)
    engine.dispose()

    # Then：整行 no-op——两个字段都保留原值，且不写 marker。
    assert row is not None
    assert row["description_is_sql_null"] is True
    assert row["description"] is None
    if policy is None:
        assert row["policy_is_sql_null"] is True
        assert row["validity_policy"] is None
    else:
        assert row["validity_policy"] == PREVIOUS_POLICY
    assert marker is None


@pytest.mark.parametrize(
    ("policy", "description"),
    [
        (CUSTOM_POLICY, PREVIOUS_DESCRIPTION),
        (PREVIOUS_POLICY, CUSTOM_DESCRIPTION),
        (CUSTOM_POLICY, CUSTOM_DESCRIPTION),
        (CUSTOM_POLICY, None),
    ],
)
def test_upgrade_custom_field_keeps_whole_row(
    migration_database: str, policy: dict[str, object], description: str | None
) -> None:
    # Given：0052 上至少一个字段是运营自定义值；description 的 SQL NULL 也按自定义处理。
    run_alembic(migration_database, "upgrade", "0052")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _set_source(connection, TYC_CODE, policy=policy, description=description)

    # When：升级到 0053。
    run_alembic(migration_database, "upgrade", "0053")
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
        marker = _marker(connection, TYC_CODE)
    engine.dispose()

    # Then：整行不迁移，两个字段都保留原值，且不写 marker。
    assert row is not None
    assert row["validity_policy"] == policy
    assert row["description"] == description
    assert marker is None


def test_upgrade_switches_target_and_keeps_other_sources(
    migration_database: str,
) -> None:
    # Given：0052 上目标行为旧默认；另一信源带自定义策略与说明。
    run_alembic(migration_database, "upgrade", "0052")
    engine = create_engine(migration_database)
    other_policy = {"mode": "until_revoked", "review_required": False}
    with engine.begin() as connection:
        _set_source(
            connection,
            TYC_CODE,
            policy=PREVIOUS_POLICY,
            description=PREVIOUS_DESCRIPTION,
        )
        _set_source(
            connection, OTHER_CODE, policy=other_policy, description=CUSTOM_DESCRIPTION
        )

    # When：升级到 0053。
    run_alembic(migration_database, "upgrade", "0053")
    with engine.connect() as connection:
        target = _source_row(connection, TYC_CODE)
        other = _source_row(connection, OTHER_CODE)
        other_marker = _marker(connection, OTHER_CODE)

    # Then：目标行按运营口径切到 until_superseded；其它信源不受影响、不写 marker。
    assert target is not None and target["validity_policy"] == TARGET_POLICY
    assert target["description"] == TARGET_DESCRIPTION
    assert other is not None and other["validity_policy"] == other_policy
    assert other["description"] == CUSTOM_DESCRIPTION
    assert other_marker is None

    # When：降级到 0052；Then：其它信源仍保持自定义值。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        other_after = _source_row(connection, OTHER_CODE)
    engine.dispose()
    assert other_after is not None and other_after["validity_policy"] == other_policy
    assert other_after["description"] == CUSTOM_DESCRIPTION


@pytest.mark.parametrize(
    (
        "rewritten_policy",
        "rewritten_description",
        "expected_policy",
        "expected_description",
    ),
    [
        (CUSTOM_POLICY, TARGET_DESCRIPTION, CUSTOM_POLICY, PREVIOUS_DESCRIPTION),
        (TARGET_POLICY, CUSTOM_DESCRIPTION, PREVIOUS_POLICY, CUSTOM_DESCRIPTION),
        (CUSTOM_POLICY, CUSTOM_DESCRIPTION, CUSTOM_POLICY, CUSTOM_DESCRIPTION),
    ],
)
def test_downgrade_restores_only_fields_still_at_target(
    migration_database: str,
    rewritten_policy: dict[str, object],
    rewritten_description: str,
    expected_policy: dict[str, object],
    expected_description: str,
) -> None:
    # Given：旧默认升级到 0053 后，运营再次改写部分或全部字段。
    run_alembic(migration_database, "upgrade", "0052")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _set_source(
            connection,
            TYC_CODE,
            policy=PREVIOUS_POLICY,
            description=PREVIOUS_DESCRIPTION,
        )
    run_alembic(migration_database, "upgrade", "0053")
    with engine.begin() as connection:
        _set_source(
            connection,
            TYC_CODE,
            policy=rewritten_policy,
            description=rewritten_description,
        )

    # When：降级到 0052。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
        marker = _marker(connection, TYC_CODE)
    engine.dispose()

    # Then：仍等于 0053 目标值的字段还原；已被运营改写的字段保留；marker 一律删除。
    assert row is not None
    assert row["validity_policy"] == expected_policy
    assert row["description"] == expected_description
    assert marker is None


def test_downgrade_missing_marker_is_noop(migration_database: str) -> None:
    # Given：升级到 head 后删除 marker（模拟 provenance 缺失）。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_sources SET login_config = login_config - :key "
                "WHERE code = :code"
            ),
            {"key": MARKER_KEY, "code": TYC_CODE},
        )

    # When：降级到 0052；Then：不猜测前态，目标值原样保留。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
    engine.dispose()
    assert row is not None
    assert row["validity_policy"] == TARGET_POLICY
    assert row["description"] == TARGET_DESCRIPTION


@pytest.mark.parametrize(
    "patch",
    [
        {"revision": "0052"},
        {"policy": None},
        {"description": None},
    ],
)
def test_downgrade_invalid_marker_is_noop(
    migration_database: str, patch: dict[str, str | None]
) -> None:
    # Given：升级到 head 后把 marker 改成无效形态（revision 不符 / 键缺失）。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _patch_marker(connection, TYC_CODE, patch)

    # When：降级到 0052；Then：无效 marker 不触发恢复，目标值保留。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
    engine.dispose()
    assert row is not None
    assert row["validity_policy"] == TARGET_POLICY
    assert row["description"] == TARGET_DESCRIPTION


def test_downgrade_non_object_marker_is_noop(migration_database: str) -> None:
    # Given：升级到 head 后把 marker 整体替换为 JSON null。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _replace_marker(connection, TYC_CODE, "null")

    # When：降级到 0052；Then：非对象 marker 不触发恢复，目标值保留。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        row = _source_row(connection, TYC_CODE)
    engine.dispose()
    assert row is not None
    assert row["validity_policy"] == TARGET_POLICY
    assert row["description"] == TARGET_DESCRIPTION


def test_upgrade_missing_target_row_is_noop(migration_database: str) -> None:
    # Given：0052 上目标行不存在。
    run_alembic(migration_database, "upgrade", "0052")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM data_sources WHERE code = :code"), {"code": TYC_CODE}
        )

    # When / Then：升级到 0053 不报错，也不凭空重建目标行。
    run_alembic(migration_database, "upgrade", "0053")
    with engine.connect() as connection:
        count = _source_count(connection, TYC_CODE)
    engine.dispose()
    assert count == 0


def test_downgrade_missing_target_row_is_noop(migration_database: str) -> None:
    # Given：升级到 0053 后删除目标行。
    run_alembic(migration_database, "upgrade", "0053")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM data_sources WHERE code = :code"), {"code": TYC_CODE}
        )

    # When / Then：降级到 0052 不报错且不重建行。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        count = _source_count(connection, TYC_CODE)
    engine.dispose()
    assert count == 0


def test_alembic_heads_is_unique_0053(migration_database: str) -> None:
    heads = _alembic_heads(migration_database)
    assert len(heads) == 1
    assert heads[0].startswith("0053")
