"""0057 排期快照与调度执行历史：事件语义、幂等、乱序、隔离与保留。

用例分层（Given/When/Then）：
1. 稳定枚举与排除集合：状态严格为 running|completed|error|missed|max_instances，
   绝不命名 succeeded；registry 排除纯内部维护 job，history 额外排除高频 notify。
2. 迁移：0057 紧接 0056 建立 scheduler_job_registry 与 scheduler_job_run。
3. 事件语义（对齐 APScheduler 3.11.3）：SUBMITTED/MAX_INSTANCES 使用复数
   scheduled_run_times，EXECUTED/ERROR/MISSED 使用单数 scheduled_run_time。
4. 幂等与乱序：唯一键 (job_id, scheduled_run_at UTC)；终态可先到并 UPSERT，
   迟到的 SUBMITTED 不得把终态改回 running；重复事件不产生新行。
5. 脱敏：事件携带的 exception/traceback/retval 一律不落库。
6. 隔离：监听器与写入异常只记稳定日志，绝不向上抛、不影响业务 job。
7. 中断收敛：Scheduler 启动时把上次进程中断遗留的 running 行收敛为稳定异常
   error（finished_at 写启动观测时间），幂等、失败隔离，不新增状态枚举。
8. 保留：执行历史按 RetentionSettings.run_days（默认 30 天）在 cleanup 清理。
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo

import pytest
from apscheduler.events import (
    EVENT_JOB_ADDED,
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_MODIFIED,
    EVENT_JOB_REMOVED,
    EVENT_JOB_SUBMITTED,
    EVENT_SCHEDULER_STARTED,
    JobEvent,
    JobExecutionEvent,
    JobSubmissionEvent,
    SchedulerEvent,
)
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import app.scheduler.events as scheduler_events
import app.scheduler.observability as scheduler_observability
from app.config import RetentionSettings
from app.scheduler.observability import JobLike, JobSource
from app.scheduler.observability_models import (
    JOB_RUN_STATUS_VALUES,
    SchedulerJobRegistry,
    SchedulerJobRun,
)
from app.scheduler.retention import cleanup_retention

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)

INTERNAL_JOB_IDS = frozenset(
    {
        "runtime-heartbeat",
        "registry-refresh",
        "research-registry-refresh",
        "research-capacity-recovery",
    }
)


def _noop() -> None:
    return None


def _scheduler_with_collect_job() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(_noop, "interval", seconds=60, id="collect", name="定时采集与处理")
    return scheduler


def _clear_observability_rows(session: Session) -> None:
    session.execute(delete(SchedulerJobRun))
    session.execute(delete(SchedulerJobRegistry))
    session.flush()


def _runs_of(session: Session, job_id: str) -> list[SchedulerJobRun]:
    return list(
        session.scalars(
            select(SchedulerJobRun)
            .where(SchedulerJobRun.job_id == job_id)
            .order_by(SchedulerJobRun.scheduled_run_at)
        )
    )


@pytest.fixture
def observability_uses_test_session(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """观测写入落在测试事务的 savepoint 内，用例结束随外层事务回滚。"""
    monkeypatch.setattr(
        scheduler_observability, "SessionLocal", lambda: nullcontext(db_session)
    )
    yield


# --------------------------------------------------------------------------- #
# 1. 稳定枚举与排除集合
# --------------------------------------------------------------------------- #
def test_job_run_status_values_are_stable_without_succeeded() -> None:
    assert JOB_RUN_STATUS_VALUES == (
        "running",
        "completed",
        "error",
        "missed",
        "max_instances",
    )
    # EXECUTED 只表示 callable 正常返回，绝不命名为 succeeded。
    assert "succeeded" not in JOB_RUN_STATUS_VALUES


def test_internal_jobs_are_excluded_from_registry_and_history() -> None:
    assert scheduler_observability.REGISTRY_EXCLUDED_JOB_IDS == INTERNAL_JOB_IDS
    assert scheduler_observability.HISTORY_EXCLUDED_JOB_IDS == INTERNAL_JOB_IDS | {"notify"}


# --------------------------------------------------------------------------- #
# 2. 迁移
# --------------------------------------------------------------------------- #
def test_migration_0057_declares_scheduler_observability_tables() -> None:
    migration_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0057_scheduler_observability.py"
    )
    text = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "0057"' in text
    assert 'down_revision: str | None = "0056"' in text
    for table in ("scheduler_job_registry", "scheduler_job_run"):
        assert f'"{table}"' in text
    for status in JOB_RUN_STATUS_VALUES:
        assert f"'{status}'" in text
    assert "scheduled_run_at" in text


# --------------------------------------------------------------------------- #
# 3. 监听器订阅与事件路由
# --------------------------------------------------------------------------- #
def test_attach_listener_subscribes_to_observability_events() -> None:
    class _RecordingScheduler:
        """最小调度器替身：结构匹配监听视图，不依赖真实调度器或事件循环。"""

        def __init__(self) -> None:
            self.calls: list[tuple[Callable[[SchedulerEvent], None], int]] = []

        def add_listener(
            self, callback: Callable[[SchedulerEvent], None], mask: int
        ) -> None:
            self.calls.append((callback, mask))

        def get_job(self, job_id: str) -> JobLike | None:
            del job_id
            return None

        def get_jobs(self) -> Sequence[JobLike]:
            return []

    scheduler = _RecordingScheduler()
    scheduler_events.attach_observability_listener(scheduler)

    assert len(scheduler.calls) == 1
    callback, mask = scheduler.calls[0]
    assert isinstance(callback, functools.partial)
    assert callback.args == (scheduler,)
    for code in (
        EVENT_SCHEDULER_STARTED,
        EVENT_JOB_ADDED,
        EVENT_JOB_MODIFIED,
        EVENT_JOB_REMOVED,
        EVENT_JOB_SUBMITTED,
        EVENT_JOB_EXECUTED,
        EVENT_JOB_ERROR,
        EVENT_JOB_MISSED,
        EVENT_JOB_MAX_INSTANCES,
    ):
        assert mask & code


def test_listener_syncs_registry_after_lifecycle_and_submitted_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    synced: list[JobSource] = []
    monkeypatch.setattr(
        scheduler_observability,
        "sync_job_registry",
        lambda target, **kwargs: synced.append(target) or True,
    )
    monkeypatch.setattr(
        scheduler_observability,
        "record_job_submitted",
        lambda *args, **kwargs: True,
    )

    events = (
        SchedulerEvent(EVENT_SCHEDULER_STARTED),
        JobEvent(EVENT_JOB_ADDED, "collect", "default"),
        JobEvent(EVENT_JOB_MODIFIED, "collect", "default"),
        JobEvent(EVENT_JOB_REMOVED, "collect", "default"),
        # 任务要求：JOB_SUBMITTED 后也要同步真实 next_run_at 快照。
        JobSubmissionEvent(EVENT_JOB_SUBMITTED, "collect", "default", [NOW]),
    )
    for event in events:
        scheduler_events.on_scheduler_event(scheduler, event)

    assert synced == [scheduler] * len(events)


def test_submitted_event_routes_plural_scheduled_run_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _scheduler_with_collect_job()
    submitted: list[tuple[str, list[datetime], str]] = []
    monkeypatch.setattr(
        scheduler_observability,
        "record_job_submitted",
        lambda job_id, times, *, name, **kwargs: submitted.append(
            (job_id, list(times), name)
        ),
    )
    monkeypatch.setattr(scheduler_observability, "sync_job_registry", lambda target: True)

    run_times = [NOW, NOW + timedelta(minutes=1)]
    scheduler_events.on_scheduler_event(
        scheduler, JobSubmissionEvent(EVENT_JOB_SUBMITTED, "collect", "default", run_times)
    )

    # 复数 scheduled_run_times 原样传递；名字从 scheduler.get_job 回退解析。
    assert submitted == [("collect", run_times, "定时采集与处理")]


def test_terminal_events_route_singular_scheduled_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = _scheduler_with_collect_job()
    outcomes: list[tuple[str, datetime, str, str]] = []
    monkeypatch.setattr(
        scheduler_observability,
        "record_job_outcome",
        lambda job_id, run_at, status, *, name, **kwargs: outcomes.append(
            (job_id, run_at, status, name)
        ),
    )

    for code in (EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED):
        scheduler_events.on_scheduler_event(
            scheduler, JobExecutionEvent(code, "collect", "default", NOW)
        )

    assert [(item[2], item[3]) for item in outcomes] == [
        ("completed", "定时采集与处理"),
        ("error", "定时采集与处理"),
        ("missed", "定时采集与处理"),
    ]
    assert all(item[1] == NOW for item in outcomes)


# --------------------------------------------------------------------------- #
# 4. 事件语义：SUBMITTED / EXECUTED / MISSED / MAX_INSTANCES
# --------------------------------------------------------------------------- #
def test_submitted_records_running_row_with_utc_normalized_run_time(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()
    # Asia/Shanghai 12:00 = UTC 04:00，落库必须规范化为 UTC。
    run_at = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    scheduler_events.on_scheduler_event(
        scheduler,
        JobSubmissionEvent(EVENT_JOB_SUBMITTED, "collect", "default", [run_at]),
    )

    rows = _runs_of(db_session, "collect")
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "running"
    assert row.name == "定时采集与处理"
    assert row.scheduled_run_at == NOW
    assert row.scheduled_run_at.tzinfo is not None
    assert row.started_at is not None
    assert row.finished_at is None


def test_submitted_then_executed_keeps_started_at(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()

    scheduler_events.on_scheduler_event(
        scheduler, JobSubmissionEvent(EVENT_JOB_SUBMITTED, "collect", "default", [NOW])
    )
    scheduler_events.on_scheduler_event(
        scheduler, JobExecutionEvent(EVENT_JOB_EXECUTED, "collect", "default", NOW)
    )

    rows = _runs_of(db_session, "collect")
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "completed"
    assert row.started_at is not None
    assert row.finished_at is not None


def test_repeated_and_out_of_order_events_keep_single_terminal_row(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()

    # Given 终态先到（乱序）：没有 SUBMITTED 也要落 completed。
    scheduler_events.on_scheduler_event(
        scheduler, JobExecutionEvent(EVENT_JOB_EXECUTED, "collect", "default", NOW)
    )
    rows = _runs_of(db_session, "collect")
    assert len(rows) == 1
    assert rows[0].status == "completed"
    assert rows[0].started_at is None
    assert rows[0].finished_at is not None

    # When 迟到的 SUBMITTED 到达：不得把终态改回 running，也不得插入新行。
    scheduler_events.on_scheduler_event(
        scheduler, JobSubmissionEvent(EVENT_JOB_SUBMITTED, "collect", "default", [NOW])
    )
    rows = _runs_of(db_session, "collect")
    assert len(rows) == 1
    assert rows[0].status == "completed"

    # When 重复的终态事件到达：仍保持单行。
    scheduler_events.on_scheduler_event(
        scheduler, JobExecutionEvent(EVENT_JOB_EXECUTED, "collect", "default", NOW)
    )
    assert len(_runs_of(db_session, "collect")) == 1


def test_max_instances_records_each_plural_run_time(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()
    run_times = [NOW, NOW + timedelta(minutes=5)]

    scheduler_events.on_scheduler_event(
        scheduler,
        JobSubmissionEvent(EVENT_JOB_MAX_INSTANCES, "collect", "default", run_times),
    )

    rows = _runs_of(db_session, "collect")
    assert [row.status for row in rows] == ["max_instances", "max_instances"]
    assert [row.scheduled_run_at for row in rows] == run_times
    # 从未运行：不编造开始/结束时间。
    assert all(row.started_at is None and row.finished_at is None for row in rows)


def test_missed_records_without_run_timestamps(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()

    scheduler_events.on_scheduler_event(
        scheduler, JobExecutionEvent(EVENT_JOB_MISSED, "collect", "default", NOW)
    )

    rows = _runs_of(db_session, "collect")
    assert len(rows) == 1
    assert rows[0].status == "missed"
    assert rows[0].started_at is None
    assert rows[0].finished_at is None


# --------------------------------------------------------------------------- #
# 5. 脱敏与列白名单
# --------------------------------------------------------------------------- #
def test_terminal_event_does_not_persist_exception_retval_or_traceback(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()

    scheduler_events.on_scheduler_event(
        scheduler,
        JobExecutionEvent(
            EVENT_JOB_ERROR,
            "collect",
            "default",
            NOW,
            retval="SECRET_RETVAL",
            exception=RuntimeError("SECRET_EXCEPTION"),
            traceback="SECRET_TRACEBACK",
        ),
    )

    # 表结构只有稳定状态与时间戳列，没有异常文本/堆栈/返回值列。
    assert set(SchedulerJobRun.__table__.columns.keys()) == {
        "id",
        "job_id",
        "name",
        "status",
        "scheduled_run_at",
        "started_at",
        "finished_at",
        "created_at",
    }
    row = _runs_of(db_session, "collect")[0]
    assert row.status == "error"
    assert "SECRET" not in repr(row.__dict__)


# --------------------------------------------------------------------------- #
# 6. 排除规则与异常隔离
# --------------------------------------------------------------------------- #
def test_history_excludes_internal_and_notify_jobs(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    for job_id in sorted(INTERNAL_JOB_IDS | {"notify"}):
        scheduler_events.on_scheduler_event(
            scheduler, JobSubmissionEvent(EVENT_JOB_SUBMITTED, job_id, "default", [NOW])
        )
        scheduler_events.on_scheduler_event(
            scheduler, JobExecutionEvent(EVENT_JOB_EXECUTED, job_id, "default", NOW)
        )
    assert db_session.scalar(select(SchedulerJobRun)) is None

    # 动态 source-* 必须记录；名字缺失时回退 job_id，不编造。
    scheduler_events.on_scheduler_event(
        scheduler, JobSubmissionEvent(EVENT_JOB_SUBMITTED, "source-7", "default", [NOW])
    )
    scheduler_events.on_scheduler_event(
        scheduler, JobExecutionEvent(EVENT_JOB_EXECUTED, "source-7", "default", NOW)
    )
    rows = _runs_of(db_session, "source-7")
    assert len(rows) == 1
    assert rows[0].status == "completed"
    assert rows[0].name == "source-7"


def test_listener_failure_is_isolated(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken_session() -> NoReturn:
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler_observability, "SessionLocal", _broken_session)
    scheduler = _scheduler_with_collect_job()

    # 不向上抛异常，业务 job 不受影响；写入返回 False 由观测层内部吞掉。
    scheduler_events.on_scheduler_event(
        scheduler, JobSubmissionEvent(EVENT_JOB_SUBMITTED, "collect", "default", [NOW])
    )
    scheduler_events.on_scheduler_event(
        scheduler, JobExecutionEvent(EVENT_JOB_EXECUTED, "collect", "default", NOW)
    )
    assert scheduler_observability.record_job_submitted("collect", [NOW], name="x") is False
    assert (
        scheduler_observability.record_job_outcome("collect", NOW, "completed", name="x")
        is False
    )


# --------------------------------------------------------------------------- #
# 7. 启动时中断收敛（APScheduler SUBMITTED 可能永远没有终态）
# --------------------------------------------------------------------------- #
def test_scheduler_started_reconciles_interrupted_running_runs(
    db_session: Session, observability_uses_test_session: None
) -> None:
    _clear_observability_rows(db_session)
    scheduler = _scheduler_with_collect_job()
    terminal_statuses = ("completed", "error", "missed", "max_instances")
    db_session.add(
        SchedulerJobRun(
            job_id="collect",
            name="定时采集与处理",
            status="running",
            scheduled_run_at=NOW,
            started_at=NOW,
        )
    )
    for index, status in enumerate(terminal_statuses, start=1):
        db_session.add(
            SchedulerJobRun(
                job_id="collect",
                name="定时采集与处理",
                status=status,
                scheduled_run_at=NOW + timedelta(minutes=index),
                started_at=NOW + timedelta(minutes=index),
                finished_at=NOW + timedelta(minutes=index + 1),
            )
        )
    db_session.flush()

    # When 进程重启（SCHEDULER_STARTED），遗留 running 收敛为 error。
    scheduler_events.on_scheduler_event(scheduler, SchedulerEvent(EVENT_SCHEDULER_STARTED))

    rows = {row.scheduled_run_at: row for row in _runs_of(db_session, "collect")}
    interrupted = rows[NOW]
    assert interrupted.status == "error"
    # started_at 保留原始登记时间；finished_at 写本次启动观测时间。
    assert interrupted.started_at == NOW
    assert interrupted.finished_at is not None

    # Then 已有终态一律不变。
    for index, status in enumerate(terminal_statuses, start=1):
        row = rows[NOW + timedelta(minutes=index)]
        assert row.status == status
        assert row.finished_at == NOW + timedelta(minutes=index + 1)

    # 重复启动观测无副作用：无 running 可收敛，其余字段逐行不变。
    before = sorted(
        (row.id, row.job_id, row.status, row.finished_at) for row in rows.values()
    )
    scheduler_events.on_scheduler_event(scheduler, SchedulerEvent(EVENT_SCHEDULER_STARTED))
    after = sorted(
        (row.id, row.job_id, row.status, row.finished_at)
        for row in _runs_of(db_session, "collect")
    )
    assert after == before
    assert all(row[2] != "running" for row in after)


def test_scheduler_started_reconciles_before_registry_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given 进程启动，When 处理 SCHEDULER_STARTED，Then 先收敛 running 再同步快照。"""
    calls: list[str] = []
    monkeypatch.setattr(
        scheduler_observability,
        "reconcile_interrupted_runs",
        lambda **kwargs: calls.append("reconcile") or True,
    )
    monkeypatch.setattr(
        scheduler_observability,
        "sync_job_registry",
        lambda target, **kwargs: calls.append("sync") or True,
    )
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    scheduler_events.on_scheduler_event(scheduler, SchedulerEvent(EVENT_SCHEDULER_STARTED))

    assert calls == ["reconcile", "sync"]


def test_reconcile_interrupted_runs_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken_session() -> NoReturn:
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler_observability, "SessionLocal", _broken_session)

    # 收敛失败只返回 False，不向上抛，也不影响启动流程。
    assert scheduler_observability.reconcile_interrupted_runs() is False


# --------------------------------------------------------------------------- #
# 8. 保留清理
# --------------------------------------------------------------------------- #
def test_cleanup_deletes_expired_job_runs_by_run_days(db_session: Session) -> None:
    _clear_observability_rows(db_session)
    expired = SchedulerJobRun(
        job_id="collect",
        name="定时采集与处理",
        status="completed",
        scheduled_run_at=NOW - timedelta(days=31),
        started_at=NOW - timedelta(days=31),
        finished_at=NOW - timedelta(days=31),
    )
    fresh = SchedulerJobRun(
        job_id="collect",
        name="定时采集与处理",
        status="completed",
        scheduled_run_at=NOW - timedelta(days=29),
        started_at=NOW - timedelta(days=29),
        finished_at=NOW - timedelta(days=29),
    )
    db_session.add_all([expired, fresh])
    db_session.flush()
    fresh_id = fresh.id

    result = cleanup_retention(
        db_session,
        RetentionSettings(signal_days=90, event_days=90, run_days=30),
        now_utc=NOW,
    )

    assert result.deleted_scheduler_runs == 1
    assert [row.id for row in _runs_of(db_session, "collect")] == [fresh_id]
