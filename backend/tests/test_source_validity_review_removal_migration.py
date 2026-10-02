"""迁移 0054（清理信源级复核配置 review_days/review_required）定向验证。

沿用 tests/test_tyc_profile_validity_migration.py 与 tests/mofcom_migration_support.py
的隔离库模式：在隔离测试库内跑真实 alembic upgrade/downgrade 往返，绝不连接或写入
当前业务库。

契约：
- upgrade：对每个 ``validity_policy ?| ARRAY['review_days','review_required']`` 的行，
  先把原策略与去 review 值写入
  ``login_config.source_validity_review_removal = {"revision":"0054",
  "policy_before":<原值>,"policy_after":<去 review 值>}``，再以 JSONB 减键清理两个
  review 键；未含 review 键的行不写 marker、不被触碰。随后重建
  ``ck_data_sources_validity_policy``（0047 文本去掉 review 键白名单与 review_required
  类型 / review_days 取值子句），残留 review 键直接被约束拒绝。
- downgrade（同一迁移事务内，顺序固定）：先 DROP 新约束；仅当 validity_policy 精确
  等于 marker.policy_after 时恢复 marker.policy_before，有效 marker 无论字段是否命中
  都删除（同 0053 语义）；再重建 0047 旧约束文本，保证 0053 的精确等值降级在 0054
  之后仍可用。
- models.py 的约束文本必须与迁移的「0047 去 review」文本一致；迁移的旧文本必须与
  0047 原文一致。
"""

from __future__ import annotations

import importlib.util
import json
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
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
MIGRATION_FILE = VERSIONS_DIR / "0054_source_validity_review_removal.py"
TYC_CODE = "tianyancha"
REVIEW_MARKER_KEY = "source_validity_review_removal"
REVIEW_CODE = "validity-review-required-source"
REVIEW_DAYS_CODE = "validity-review-days-source"
CLEAN_CODE = "validity-clean-source"

POLICY_WITH_REVIEW_REQUIRED: dict[str, object] = {
    "mode": "until_superseded",
    "fixed_days": 30,
    "review_required": True,
}
POLICY_CLEAN: dict[str, object] = {
    "mode": "until_superseded",
    "fixed_days": 30,
}
POLICY_WITH_REVIEW_DAYS: dict[str, object] = {
    "mode": "until_revoked",
    "review_days": 90,
}
POLICY_REVIEW_DAYS_REMOVED: dict[str, object] = {
    "mode": "until_revoked",
}
REWRITTEN_POLICY: dict[str, object] = {
    "mode": "indefinite",
}


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_module("migration_0054", MIGRATION_FILE)
_MIGRATION_0053 = _load_module(
    "migration_0053", VERSIONS_DIR / "0053_tyc_profile_validity.py"
)
_MIGRATION_0047 = _load_module(
    "migration_0047", VERSIONS_DIR / "0047_risk_signal_validity_policy.py"
)
TARGET_CHECK: str = _MIGRATION.SOURCE_POLICY_CHECK
LEGACY_CHECK: str = _MIGRATION.LEGACY_SOURCE_POLICY_CHECK
LEGACY_CHECK_0047: str = _MIGRATION_0047.SOURCE_POLICY_CHECK
TARGET_POLICY_0053: dict[str, object] = _MIGRATION_0053.TARGET_POLICY


def _normalize(sql_text: str) -> str:
    return " ".join(sql_text.split())


@pytest.fixture
def migration_database() -> Generator[str]:
    with isolated_database() as database_url:
        yield database_url


def _seed_source(
    connection: Connection,
    code: str,
    *,
    policy: dict[str, object] | None,
    login_config: str = "{}",
) -> None:
    """插入一行 data_sources 并设置 validity_policy 与 login_config。"""
    source_id = insert_source(
        connection,
        SourceSeed(
            code=code,
            schedule="0 6 * * *",
            enabled=True,
            adapter_status="published",
        ),
    )
    connection.execute(
        text(
            "UPDATE data_sources SET validity_policy = CAST(:policy AS jsonb), "
            "login_config = CAST(:config AS jsonb) WHERE id = :id"
        ),
        {
            "policy": None if policy is None else json.dumps(policy),
            "config": login_config,
            "id": source_id,
        },
    )


def _policy(connection: Connection, code: str) -> object:
    raw = connection.scalar(
        text("SELECT validity_policy::text FROM data_sources WHERE code = :code"),
        {"code": code},
    )
    assert raw is not None
    return json.loads(raw)


def _source_row(connection: Connection, code: str) -> dict[str, object] | None:
    row = connection.execute(
        text(
            "SELECT validity_policy, validity_policy IS NULL AS policy_is_sql_null "
            "FROM data_sources WHERE code = :code"
        ),
        {"code": code},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def _marker(connection: Connection, code: str) -> dict[str, object] | None:
    """读取 0054 marker；键缺失、JSON null 或非对象时返回 None。"""
    raw = connection.scalar(
        text("SELECT (login_config->:key)::text FROM data_sources WHERE code = :code"),
        {"key": REVIEW_MARKER_KEY, "code": code},
    )
    if raw is None:
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None


def _login_config(connection: Connection, code: str) -> dict[str, object]:
    raw = connection.scalar(
        text("SELECT login_config::text FROM data_sources WHERE code = :code"),
        {"code": code},
    )
    assert raw is not None
    return json.loads(raw)


def _set_policy(connection: Connection, code: str, policy: dict[str, object]) -> None:
    connection.execute(
        text(
            "UPDATE data_sources SET validity_policy = CAST(:policy AS jsonb) "
            "WHERE code = :code"
        ),
        {"policy": json.dumps(policy), "code": code},
    )


def test_upgrade_cleans_review_keys_and_records_provenance(
    migration_database: str,
) -> None:
    # Given：0053 上三行——含 review_required、含 review_days、干净策略。
    run_alembic(migration_database, "upgrade", "0053")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_source(
            connection,
            REVIEW_CODE,
            policy=POLICY_WITH_REVIEW_REQUIRED,
            login_config='{"keep":"yes"}',
        )
        _seed_source(connection, REVIEW_DAYS_CODE, policy=POLICY_WITH_REVIEW_DAYS)
        _seed_source(connection, CLEAN_CODE, policy=POLICY_CLEAN)

    # When：升级到 0054。
    run_alembic(migration_database, "upgrade", "0054")
    with engine.connect() as connection:
        review_policy = _policy(connection, REVIEW_CODE)
        review_marker = _marker(connection, REVIEW_CODE)
        config = _login_config(connection, REVIEW_CODE)
        days_policy = _policy(connection, REVIEW_DAYS_CODE)
        days_marker = _marker(connection, REVIEW_DAYS_CODE)
        clean_policy = _policy(connection, CLEAN_CODE)
        clean_marker = _marker(connection, CLEAN_CODE)
    engine.dispose()

    # Then：含 review 键的行被清理，marker 精确记录 before/after。
    assert review_policy == POLICY_CLEAN
    assert review_marker == {
        "revision": "0054",
        "policy_before": POLICY_WITH_REVIEW_REQUIRED,
        "policy_after": POLICY_CLEAN,
    }
    assert days_policy == POLICY_REVIEW_DAYS_REMOVED
    assert days_marker == {
        "revision": "0054",
        "policy_before": POLICY_WITH_REVIEW_DAYS,
        "policy_after": POLICY_REVIEW_DAYS_REMOVED,
    }
    # Then：干净行不被触碰、不写 marker；login_config 其它键保留（jsonb_set 不覆盖）。
    assert clean_policy == POLICY_CLEAN
    assert clean_marker is None
    assert config.get("keep") == "yes"


def test_downgrade_restores_original_policy_and_removes_marker(
    migration_database: str,
) -> None:
    # Given：两行含 review 键的策略被 0054 清理。
    run_alembic(migration_database, "upgrade", "0053")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_source(connection, REVIEW_CODE, policy=POLICY_WITH_REVIEW_REQUIRED)
        _seed_source(connection, REVIEW_DAYS_CODE, policy=POLICY_WITH_REVIEW_DAYS)
    run_alembic(migration_database, "upgrade", "0054")

    # When：降级到 0053。
    run_alembic(migration_database, "downgrade", "0053")
    with engine.connect() as connection:
        review_policy = _policy(connection, REVIEW_CODE)
        review_marker = _marker(connection, REVIEW_CODE)
        days_policy = _policy(connection, REVIEW_DAYS_CODE)
        days_marker = _marker(connection, REVIEW_DAYS_CODE)
    engine.dispose()

    # Then：原值精确还原（含 review 键），marker 删除。
    assert review_policy == POLICY_WITH_REVIEW_REQUIRED
    assert review_marker is None
    assert days_policy == POLICY_WITH_REVIEW_DAYS
    assert days_marker is None


def test_downgrade_keeps_rewritten_policy_and_removes_marker(
    migration_database: str,
) -> None:
    # Given：0054 后运营把策略改写为不等于 policy_after 的自定义值。
    run_alembic(migration_database, "upgrade", "0053")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_source(connection, REVIEW_CODE, policy=POLICY_WITH_REVIEW_REQUIRED)
    run_alembic(migration_database, "upgrade", "0054")
    with engine.begin() as connection:
        _set_policy(connection, REVIEW_CODE, REWRITTEN_POLICY)

    # When：降级到 0053。
    run_alembic(migration_database, "downgrade", "0053")
    with engine.connect() as connection:
        policy = _policy(connection, REVIEW_CODE)
        marker = _marker(connection, REVIEW_CODE)
    engine.dispose()

    # Then：运营改写值保留，有效 marker 一律删除（不猜测前态）。
    assert policy == REWRITTEN_POLICY
    assert marker is None


@pytest.mark.parametrize("mutation", ["missing", "invalid_revision"])
def test_downgrade_missing_or_invalid_marker_is_noop(
    migration_database: str, mutation: str
) -> None:
    # Given：0054 后 marker 缺失或 revision 不符（模拟 provenance 缺失/无效）。
    run_alembic(migration_database, "upgrade", "0053")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_source(connection, REVIEW_CODE, policy=POLICY_WITH_REVIEW_REQUIRED)
    run_alembic(migration_database, "upgrade", "0054")
    with engine.begin() as connection:
        if mutation == "missing":
            connection.execute(
                text(
                    "UPDATE data_sources SET login_config = login_config - :key "
                    "WHERE code = :code"
                ),
                {"key": REVIEW_MARKER_KEY, "code": REVIEW_CODE},
            )
        else:
            connection.execute(
                text(
                    "UPDATE data_sources SET login_config = jsonb_set("
                    f"login_config, '{{{REVIEW_MARKER_KEY}}}', "
                    "CAST('\"0053\"' AS jsonb), true) WHERE code = :code"
                ),
                {"code": REVIEW_CODE},
            )

    # When：降级到 0053。
    run_alembic(migration_database, "downgrade", "0053")
    with engine.connect() as connection:
        policy = _policy(connection, REVIEW_CODE)
    engine.dispose()

    # Then：不猜测前态，清理后的现值保留。
    assert policy == POLICY_CLEAN


@pytest.mark.parametrize(
    "rejected_policy", [POLICY_WITH_REVIEW_REQUIRED, POLICY_WITH_REVIEW_DAYS]
)
def test_new_constraint_rejects_review_keys(
    migration_database: str, rejected_policy: dict[str, object]
) -> None:
    # Given：0054 上的干净行。
    run_alembic(migration_database, "upgrade", "0054")
    engine = create_engine(migration_database)
    with engine.begin() as connection:
        _seed_source(connection, CLEAN_CODE, policy=POLICY_CLEAN)

    # When / Then：任何残留 review 键都被新约束拒绝。
    for raw_policy in (
        json.dumps(rejected_policy),
        json.dumps({"mode": "until_revoked", "review_required": False}),
    ):
        with engine.connect() as connection:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "UPDATE data_sources SET validity_policy = CAST(:policy AS jsonb) "
                        "WHERE code = :code"
                    ),
                    {"policy": raw_policy, "code": CLEAN_CODE},
                )
            connection.rollback()

    # Then：无 review 键的合法策略仍可写入（约束未误伤）。
    with engine.begin() as connection:
        _set_policy(connection, CLEAN_CODE, POLICY_REVIEW_DAYS_REMOVED)
        policy = _policy(connection, CLEAN_CODE)
    engine.dispose()
    assert policy == POLICY_REVIEW_DAYS_REMOVED


def test_downgrade_chain_0054_to_0053_to_0052_stays_exact(
    migration_database: str,
) -> None:
    # Given：fresh 链到 head（0054），0053 目标态被清理为去 review 值。
    run_alembic(migration_database, "upgrade", "head")
    engine = create_engine(migration_database)
    with engine.connect() as connection:
        head_policy = _policy(connection, TYC_CODE)

    # Then：head 态 = 0053 目标去掉 review_required。
    assert head_policy == {
        "mode": "until_superseded",
        "fixed_days": 30,
    }

    # When：只降 0054（到 0053）。
    run_alembic(migration_database, "downgrade", "0053")
    with engine.connect() as connection:
        policy_0053 = _policy(connection, TYC_CODE)

    # Then：精确等于 0053 目标（含 review_required），0053 的等值降级仍可用（O2）。
    assert policy_0053 == TARGET_POLICY_0053

    # When：继续降到 0052；Then：fresh-chain 的 SQL NULL 精确还原。
    run_alembic(migration_database, "downgrade", "0052")
    with engine.connect() as connection:
        restored = _source_row(connection, TYC_CODE)
        marker = _marker(connection, TYC_CODE)
    engine.dispose()
    assert restored is not None
    assert restored["policy_is_sql_null"] is True
    assert restored["validity_policy"] is None
    assert marker is None


def test_models_constraint_text_matches_migration() -> None:
    from app.signals.models import DataSource as ModelDataSource

    constraint = next(
        (
            item
            for item in ModelDataSource.__table__.constraints
            if item.name == "ck_data_sources_validity_policy"
        ),
        None,
    )
    assert constraint is not None
    assert _normalize(str(constraint.sqltext)) == _normalize(TARGET_CHECK)


def test_migration_legacy_check_matches_0047_text() -> None:
    assert _normalize(LEGACY_CHECK) == _normalize(LEGACY_CHECK_0047)
