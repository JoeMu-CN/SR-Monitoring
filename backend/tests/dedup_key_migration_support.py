"""迁移 0060（稳定去重身份 dedup_key）定向测试的公共支持。

文件名不以 ``test_`` 开头，pytest 默认不收集；仅供
``test_dedup_key_migration.py`` 导入使用。隔离库模式沿用
``mofcom_migration_support``：绝不连接或写入当前业务库。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url
from test_stack_guard import require_test_database_url

from app.config import DATABASE_URL

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "0059"
MOFCOM_CODE = "mofcom-entity-detail"
USGS_CODE = "usgs-earthquake-day"
MANUAL_CODE = "manual-json"


def run_alembic(database_url: str, action: str, revision: str) -> None:
    """在隔离库上执行 ``alembic <action> <revision>``（失败即抛 CalledProcessError）。"""
    subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


@contextmanager
def isolated_database() -> Generator[str]:
    """创建隔离测试库并返回其 DATABASE_URL，退出时终止连接并删除。"""
    require_test_database_url(DATABASE_URL)
    base_url = make_url(DATABASE_URL)
    database_name = f"dedup_key_{uuid4().hex[:12]}_test"
    admin_url = base_url.set(drivername="postgresql", database="postgres")
    database_url = base_url.set(database=database_name).render_as_string(
        hide_password=False
    )

    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )
    try:
        yield database_url
    finally:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )


def source_id(connection: Connection, code: str) -> int:
    return connection.execute(
        text("SELECT id FROM data_sources WHERE code = :code"), {"code": code}
    ).scalar_one()


def insert_signal(
    connection: Connection,
    *,
    source: int,
    external_id: str | None,
    title: str,
    content: str = "历史正文",
    url: str | None = None,
    fingerprint: str,
    dedup_key: str | None = None,
    validity_state: str = "pending_classification",
    valid_from: str | None = None,
) -> int:
    """插入一条 raw_signals 行，返回其 id。

    - 0060 之前（0059）无 ``dedup_key`` 列：``dedup_key`` 传 None 时不写该列；
    - 升级到 head 后该列 NOT NULL：调用方须显式传 ``dedup_key``；
    - 非 pending/irrelevant/legacy 的 ``validity_state`` 受
      ``ck_raw_signals_policy_snapshot`` 约束，这里补最小合法策略字段。
    """
    columns = [
        "source_id",
        "external_id",
        "title",
        "content",
        "url",
        "fingerprint",
        "validity_state",
        "valid_from",
        "raw_data",
    ]
    values = [
        ":source_id",
        ":external_id",
        ":title",
        ":content",
        ":url",
        ":fingerprint",
        ":validity_state",
        "CAST(:valid_from AS timestamptz)",
        "'{}'::jsonb",
    ]
    params: dict[str, object] = {
        "source_id": source,
        "external_id": external_id,
        "title": title,
        "content": content,
        "url": url,
        "fingerprint": fingerprint,
        "validity_state": validity_state,
        "valid_from": valid_from,
    }
    if dedup_key is not None:
        columns.append("dedup_key")
        values.append(":dedup_key")
        params["dedup_key"] = dedup_key
    if validity_state not in {"pending_classification", "irrelevant", "legacy"}:
        columns += ["validity_profile", "validity_mode", "validity_policy_version"]
        values += [":validity_profile", ":validity_mode", ":validity_policy_version"]
        params["validity_profile"] = "other"
        params["validity_mode"] = "indefinite"
        params["validity_policy_version"] = "test"
    statement = (
        f"INSERT INTO raw_signals ({', '.join(columns)}) "
        f"VALUES ({', '.join(values)}) RETURNING id"
    )
    return connection.execute(text(statement), params).scalar_one()


def ensure_manual_source(connection: Connection) -> int:
    """0013 播种的 manual-json 可能停用/缺行，测试只需一个可挂信号的 source。"""
    return connection.execute(
        text("SELECT id FROM data_sources WHERE code = :code"), {"code": MANUAL_CODE}
    ).scalar_one()


def signal_keys(connection: Connection, source: int) -> list[tuple[int, str]]:
    """按 id 返回 (id, dedup_key)，用于断言碰撞后缀与顺序。"""
    rows = connection.execute(
        text(
            "SELECT id, dedup_key FROM raw_signals WHERE source_id = :source ORDER BY id"
        ),
        {"source": source},
    ).all()
    return [(int(row[0]), str(row[1])) for row in rows]


def null_key_count(connection: Connection) -> int:
    return connection.execute(
        text("SELECT count(*) FROM raw_signals WHERE dedup_key IS NULL")
    ).scalar_one()


def duplicate_key_groups(connection: Connection) -> int:
    """仍存在重复 (source_id, dedup_key) 的分组数；唯一约束后必须为 0。"""
    return connection.execute(
        text(
            "SELECT count(*) FROM (SELECT source_id, dedup_key FROM raw_signals "
            "GROUP BY source_id, dedup_key HAVING count(*) > 1) AS duplicated"
        )
    ).scalar_one()


def orphan_reference_count(connection: Connection) -> int:
    """指向不存在 raw_signals 的外键行数；回填不得删除任何行，故必须为 0。"""
    return connection.execute(
        text(
            "SELECT (SELECT count(*) FROM ai_analysis_records ar "
            "  LEFT JOIN raw_signals rs ON rs.id = ar.signal_id WHERE ar.signal_id IS NOT NULL "
            "  AND rs.id IS NULL) + "
            "(SELECT count(*) FROM risk_event_signals es "
            "  LEFT JOIN raw_signals rs ON rs.id = es.signal_id "
            "  WHERE rs.id IS NULL)"
        )
    ).scalar_one()