"""任务 6：完整快照成员状态机测试（迁移 + 服务集成 + 适配器契约）。"""

# noqa: SIZE_OK — 真实 PostgreSQL 往返迁移测试需内含隔离库生命周期和完整历史夹具。

import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Connection, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_stack_guard import require_test_database_url

from app.config import DATABASE_URL
from app.signals.models import DataSource, RawSignal, SourceMemberState
from app.signals.schemas import ManualSignalInput
from app.signals.service import collect_source
from app.signals.sources import (
    BisEntityListAdapter,
    OfacSdnAdapter,
    PullSourceAdapter,
    RawSourceItem,
    UflpaEntityAdapter,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ── 迁移测试（隔离数据库往返） ───────────────────────────────────────


def _run_alembic(
    database_url: str, action: str, revision: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


@contextmanager
def _isolated_database() -> Generator[str]:
    require_test_database_url(DATABASE_URL)
    base_url = make_url(DATABASE_URL)
    database_name = f"membership_{uuid4().hex[:12]}_test"
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
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s",
                (database_name,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )


@pytest.fixture
def migration_database() -> Generator[str]:
    with _isolated_database() as database_url:
        yield database_url


def _seed_source(connection: Connection) -> int:
    return connection.execute(
        text(
            "INSERT INTO data_sources (code, name, source_type, credibility, enabled) "
            "VALUES ('membership-source', 'Membership Source', 'test', 80, true) "
            "RETURNING id"
        )
    ).scalar_one()


def test_membership_migration_round_trip(migration_database: str) -> None:
    # Given: 升级到 head 后存在成员状态表与快照字段。
    _run_alembic(migration_database, "upgrade", "head")
    isolated_engine = create_engine(migration_database)
    with isolated_engine.begin() as connection:
        source_id = _seed_source(connection)
        connection.execute(
            text(
                "INSERT INTO source_member_states "
                "(source_id, member_key, status, first_seen_at, last_seen_at, "
                "consecutive_missing, baseline_snapshot_hash) "
                "VALUES (:source_id, 'member-1', 'active', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', 0, 'hash-1')"
            ),
            {"source_id": source_id},
        )
        connection.execute(
            text(
                "INSERT INTO collection_runs "
                "(source_id, status, snapshot_complete, snapshot_hash, "
                "snapshot_quality, member_count, new_member_count, "
                "revoked_member_count, quarantine_round) "
                "VALUES (:source_id, 'succeeded', true, 'hash-1', 'complete', "
                "1, 1, 0, 0)"
            ),
            {"source_id": source_id},
        )

    # When: downgrade -> upgrade。
    _run_alembic(migration_database, "downgrade", "0047")
    with isolated_engine.connect() as connection:
        assert "source_member_states" not in inspect(connection).get_table_names()
        assert "snapshot_complete" not in {
            column["name"]
            for column in inspect(connection).get_columns("collection_runs")
        }
    _run_alembic(migration_database, "upgrade", "head")

    # Then: 表与字段重建（downgrade 删表，upgrade 后为空表）。
    with isolated_engine.connect() as connection:
        assert "source_member_states" in inspect(connection).get_table_names()
        assert "snapshot_complete" in {
            column["name"]
            for column in inspect(connection).get_columns("collection_runs")
        }
        assert connection.scalar(
            text("SELECT count(*) FROM source_member_states")
        ) == 0
    isolated_engine.dispose()


def test_membership_constraints_reject_invalid(migration_database: str) -> None:
    # Given
    _run_alembic(migration_database, "upgrade", "head")
    isolated_engine = create_engine(migration_database)
    with isolated_engine.begin() as connection:
        source_id = _seed_source(connection)

    # When / Then: 非法状态、负计数、空成员键、非法快照质量与负计数均被拒绝。
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_member_states "
                "(source_id, member_key, status, first_seen_at, last_seen_at, "
                "consecutive_missing, baseline_snapshot_hash) "
                "VALUES (:source_id, 'bad-status', 'unknown', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', 0, 'hash-1')"
            ),
            {"source_id": source_id},
        )
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_member_states "
                "(source_id, member_key, status, first_seen_at, last_seen_at, "
                "consecutive_missing, baseline_snapshot_hash) "
                "VALUES (:source_id, 'neg-missing', 'active', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', -1, 'hash-1')"
            ),
            {"source_id": source_id},
        )
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_member_states "
                "(source_id, member_key, status, first_seen_at, last_seen_at, "
                "consecutive_missing, baseline_snapshot_hash) "
                "VALUES (:source_id, '', 'active', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-01 00:00:00+00', 0, 'hash-1')"
            ),
            {"source_id": source_id},
        )
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO collection_runs "
                "(source_id, status, snapshot_quality, member_count) "
                "VALUES (:source_id, 'succeeded', 'bogus', 1)"
            ),
            {"source_id": source_id},
        )
    with pytest.raises(IntegrityError), isolated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO collection_runs "
                "(source_id, status, snapshot_quality, member_count) "
                "VALUES (:source_id, 'succeeded', 'complete', -1)"
            ),
            {"source_id": source_id},
        )
    isolated_engine.dispose()


# ── 服务集成测试（成员状态机接入采集管线） ───────────────────────────


class _FakeFullSnapshotAdapter(PullSourceAdapter):
    """模拟官方全量名单适配器（authoritative_full_snapshot=True）。"""

    source_code = "fake-full-snapshot"
    authoritative_full_snapshot = True

    def __init__(self, member_ids: list[str]) -> None:
        self._member_ids = member_ids

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        return [
            RawSourceItem(
                external_id=member_id,
                title=f"成员 {member_id}",
                content=f"官方名单成员 {member_id}",
            )
            for member_id in self._member_ids
        ]

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        return ManualSignalInput(
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            published_at=datetime.now(UTC),
        )

    def fingerprint(self, signal: ManualSignalInput) -> str:
        return f"fp-{signal.external_id}"

    async def healthcheck(self) -> object:
        return object()


class _FakePartialAdapter(_FakeFullSnapshotAdapter):
    """模拟非权威适配器（分页/增量，不声明完整快照）。"""

    authoritative_full_snapshot = False


def _get_membership_source(session: Session) -> DataSource:
    source = DataSource(
        code="membership-test-source",
        name="成员状态机测试信源",
        source_type="official_api",
        credibility=95,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
        validity_policy={
            "profile": "sanctions",
            "mode": "until_revoked",
            "review_required": False,
        },
    )
    session.add(source)
    session.flush()
    return source


def test_snapshot_revokes_member_after_two_consecutive_misses(
    db_session: Session,
) -> None:
    # Given: 全量基线 {member-0..member-9}。
    source = _get_membership_source(db_session)
    members = [f"member-{i}" for i in range(10)]
    first = collect_source(db_session, source, _FakeFullSnapshotAdapter(members))
    assert first.created_count == 10
    assert first.snapshot_complete is True
    assert first.member_count == 10

    # When: 连续两次缺失 member-9。
    second = collect_source(
        db_session, source, _FakeFullSnapshotAdapter(members[:-1])
    )
    assert second.revoked_member_count == 0
    third = collect_source(
        db_session, source, _FakeFullSnapshotAdapter(members[:-1])
    )

    # Then: 第二次缺失即撤销，信号与成员状态同步置 revoked。
    assert third.revoked_member_count == 1
    revoked = db_session.scalar(
        select(RawSignal).where(
            RawSignal.source_id == source.id,
            RawSignal.external_id == "member-9",
        )
    )
    assert revoked is not None
    assert revoked.validity_state == "revoked"
    assert revoked.validity_reason["code"] == "two_consecutive_misses"
    state = db_session.scalar(
        select(SourceMemberState).where(
            SourceMemberState.source_id == source.id,
            SourceMemberState.member_key == "member-9",
        )
    )
    assert state is not None
    assert state.status == "revoked"


def test_snapshot_quarantine_promotes_after_three_stable_rounds(
    db_session: Session,
) -> None:
    # Given: 基线 100 成员。
    source = _get_membership_source(db_session)
    baseline = [f"member-{i}" for i in range(100)]
    reduced = [f"member-{i}" for i in range(60)]
    collect_source(db_session, source, _FakeFullSnapshotAdapter(baseline))

    # When: 三轮稳定缩减 100→60。
    first = collect_source(db_session, source, _FakeFullSnapshotAdapter(reduced))
    assert first.quarantine_round == 1
    assert first.revoked_member_count == 0
    second = collect_source(db_session, source, _FakeFullSnapshotAdapter(reduced))
    assert second.quarantine_round == 2
    third = collect_source(db_session, source, _FakeFullSnapshotAdapter(reduced))

    # Then: 第三轮提升候选为新基线并撤销 40 个三轮均缺失成员。
    assert third.revoked_member_count == 40
    assert third.quarantine_round == 0
    revoked_count = db_session.scalar(
        select(RawSignal)
        .where(
            RawSignal.source_id == source.id,
            RawSignal.validity_state == "revoked",
        )
        .with_only_columns(RawSignal.id)
    )
    assert revoked_count is not None


def test_snapshot_unstable_quarantine_not_promoted(db_session: Session) -> None:
    # Given: 基线 100 成员，第一轮缩减进入隔离。
    source = _get_membership_source(db_session)
    baseline = [f"member-{i}" for i in range(100)]
    reduced = [f"member-{i}" for i in range(60)]
    collect_source(db_session, source, _FakeFullSnapshotAdapter(baseline))
    collect_source(db_session, source, _FakeFullSnapshotAdapter(reduced))

    # When: 第二轮缺失集合变化（member-59 回归、member-0 缺失）。
    shifted = [f"member-{i}" for i in range(1, 61)]
    run = collect_source(db_session, source, _FakeFullSnapshotAdapter(shifted))

    # Then: 候选重置，成员保持有效。
    assert run.revoked_member_count == 0
    assert run.quarantine_round == 0
    active = db_session.scalar(
        select(RawSignal)
        .where(
            RawSignal.source_id == source.id,
            RawSignal.validity_state == "active",
        )
        .with_only_columns(RawSignal.id)
    )
    assert active is not None


def test_snapshot_non_authoritative_never_revokes(db_session: Session) -> None:
    # Given: 全量基线后改用非权威适配器。
    source = _get_membership_source(db_session)
    members = [f"member-{i}" for i in range(10)]
    collect_source(db_session, source, _FakeFullSnapshotAdapter(members))
    partial = _FakePartialAdapter(members[:-1])

    # When: 非权威适配器连续两次缺失。
    first = collect_source(db_session, source, partial)
    second = collect_source(db_session, source, partial)

    # Then: 不撤销，快照标记为不完整。
    assert first.snapshot_complete is False
    assert second.revoked_member_count == 0
    assert second.snapshot_complete is False


def test_snapshot_empty_never_revokes(db_session: Session) -> None:
    # Given: 全量基线后返回空快照。
    source = _get_membership_source(db_session)
    members = [f"member-{i}" for i in range(10)]
    collect_source(db_session, source, _FakeFullSnapshotAdapter(members))

    # When: 空快照。
    run = collect_source(db_session, source, _FakeFullSnapshotAdapter([]))

    # Then: 不撤销任何成员。
    assert run.revoked_member_count == 0
    assert run.member_count == 0


# ── 适配器契约 ───────────────────────────────────────────────────────


def test_official_full_list_adapters_declare_authoritative_snapshot() -> None:
    assert OfacSdnAdapter.authoritative_full_snapshot is True
    assert UflpaEntityAdapter.authoritative_full_snapshot is True
    assert BisEntityListAdapter.authoritative_full_snapshot is True