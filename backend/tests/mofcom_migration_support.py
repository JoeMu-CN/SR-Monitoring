"""迁移 0050 定向测试的公共支持：隔离库、Alembic 运行与 seed/query 辅助。

文件名不以 ``test_`` 开头，pytest 默认不收集；仅供
``test_mofcom_source_consolidation_migration.py`` 导入使用。沿用
tests/test_signal_validity_migration.py 的隔离库模式，绝不连接或写入当前业务库。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url
from test_stack_guard import require_test_database_url

from app.config import DATABASE_URL

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = (
    BACKEND_ROOT / "alembic" / "versions" / "0050_mofcom_source_consolidation.py"
)
CONTROL_CODE = "mofcom-entity-control"
DETAIL_CODE = "mofcom-entity-detail"
DETAIL_ENDPOINT_URL = "http://aqygzj.mofcom.gov.cn/"
# 0013 播种 control 时的官方入口；0050 upgrade 不得改动，downgrade 后旧连接器据此恢复。
CONTROL_ENDPOINT_URL = "https://www.mofcom.gov.cn/"
# 业务态 control 的已发布声明式配置；upgrade 停用后必须原样保留，downgrade 后方可被旧代码读取。
CONTROL_PUBLISHED_ADAPTER_CONFIG: dict[str, str] = {"format": "html"}


@dataclass(frozen=True, slots=True)
class SourceSeed:
    """新建 data_sources 行的确定性参数。"""

    code: str
    schedule: str | None
    enabled: bool
    adapter_status: str
    endpoint_url: str = DETAIL_ENDPOINT_URL
    adapter_config: str = "{}"


# 业务态与缺 control 两个场景共用的 detail 初始态（builtin、启用、每 12 小时）。
DETAIL_SEED = SourceSeed(
    code=DETAIL_CODE,
    schedule="0 */12 * * *",
    enabled=True,
    adapter_status="builtin",
)


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0050", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STALE_RUN_RECOVERY_ERROR: str = _load_migration_module().STALE_RUN_RECOVERY_ERROR


def run_alembic(
    database_url: str, action: str, revision: str
) -> subprocess.CompletedProcess[str]:
    """在隔离库上执行 ``alembic <action> <revision>``。"""
    return subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


@contextmanager
def isolated_database() -> Generator[str]:
    """创建隔离测试库并返回其 DATABASE_URL，退出时在 finally 中终止连接并删除。"""
    require_test_database_url(DATABASE_URL)
    base_url = make_url(DATABASE_URL)
    database_name = f"mofcom_consolidation_{uuid4().hex[:12]}_test"
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


def insert_source(connection: Connection, seed: SourceSeed) -> int:
    """按 seed 插入一行 data_sources，返回其 id。"""
    return connection.execute(
        text(
            """
            INSERT INTO data_sources (
                code, name, source_type, credibility, schedule, endpoint_url,
                enabled, adapter_status, adapter_config
            ) VALUES (
                :code, :code, 'sanctions', 98, :schedule, :url,
                :enabled, :adapter_status, CAST(:adapter_config AS jsonb)
            )
            RETURNING id
            """
        ),
        {
            "code": seed.code,
            "schedule": seed.schedule,
            "url": seed.endpoint_url,
            "enabled": seed.enabled,
            "adapter_status": seed.adapter_status,
            "adapter_config": seed.adapter_config,
        },
    ).scalar_one()


def add_run(connection: Connection, source_id: int, status: str, age_seconds: int) -> int:
    """插入一条相对 now() 回溯 ``age_seconds`` 的采集运行，返回其 id。"""
    return connection.execute(
        text(
            "INSERT INTO collection_runs (source_id, status, started_at) "
            "VALUES (:source_id, :status, now() - make_interval(secs => :age)) "
            "RETURNING id"
        ),
        {"source_id": source_id, "status": status, "age": age_seconds},
    ).scalar_one()


def seed_business_state(connection: Connection) -> dict[str, int]:
    """模拟当前业务库：control 发布启用（daily 03:00），detail 内置启用（每 12h），
    两源各有历史信号与运行记录，其中含过期 running。返回各实体 id。"""
    control_id = connection.execute(
        text("SELECT id FROM data_sources WHERE code = :code"), {"code": CONTROL_CODE}
    ).scalar_one()
    connection.execute(
        text(
            """
            UPDATE data_sources
            SET enabled = true, schedule = '0 3 * * *',
                adapter_status = 'published',
                adapter_config = '{"format":"html"}'::jsonb
            WHERE code = :code
            """
        ),
        {"code": CONTROL_CODE},
    )
    detail_id = insert_source(connection, DETAIL_SEED)
    for source_id, fingerprint in (
        (control_id, "control-signal"),
        (detail_id, "detail-signal"),
    ):
        connection.execute(
            text(
                "INSERT INTO raw_signals (source_id, title, content, fingerprint, raw_data) "
                "VALUES (:source_id, :title, 'content', :fingerprint, '{}'::jsonb)"
            ),
            {"source_id": source_id, "title": fingerprint, "fingerprint": fingerprint},
        )
    return {
        "control_id": control_id,
        "detail_id": detail_id,
        "control_stale_run": add_run(connection, control_id, "running", 45 * 60),
        "control_fresh_run": add_run(connection, control_id, "running", 5 * 60),
        "control_done_run": add_run(connection, control_id, "succeeded", 3 * 60 * 60),
        "detail_stale_run": add_run(connection, detail_id, "running", 90 * 60),
        "detail_done_run": add_run(connection, detail_id, "succeeded", 6 * 60 * 60),
    }


def source_row(connection: Connection, code: str) -> dict[str, object]:
    """按 code 读取 data_sources 关注字段。"""
    row = connection.execute(
        text(
            "SELECT id, enabled, schedule, adapter_status, endpoint_url, adapter_config "
            "FROM data_sources WHERE code = :code"
        ),
        {"code": code},
    ).mappings().one()
    return dict(row)


def run_row(connection: Connection, run_id: int) -> dict[str, object]:
    """按 id 读取 collection_runs 关注字段。"""
    row = connection.execute(
        text("SELECT status, finished_at, error FROM collection_runs WHERE id = :id"),
        {"id": run_id},
    ).mappings().one()
    return dict(row)
