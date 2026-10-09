"""任务7（返修）：持久化监控观测与只读健康聚合，对齐计划 196-208 行契约。

用例分层：
1. 基线：固定改动前的现有行为（``/system/health`` 兼容、pending 候选谓词、
   scheduler 注册表增改删），未改生产代码前即应通过。
2. 迁移与模型：0049 表形状对齐计划列（``last_success_at``/``heartbeat_at``/
   ``error_code``）与 0048→0049 单链声明；真实 0048→0049 增量升级与空库
   upgrade head 两条路径由隔离迁移演练证据覆盖（见证据目录 migration-drill）。
3. 契约：顶层 ``as_of/overall/scheduler/processing/sources`` 与每来源
   ``source_id/last_success_at/last_attempt_at/next_expected_at/state/reason_code``；
   overall 四态真值表（ok|degraded|unknown|inactive）。
4. 口径：``schedule=NULL`` 拉取源用 ``SCHEDULER_COLLECT_CRON``；
   overdue 宽限 300 秒边界；锚点优先级（runtime 成功 > collection_runs 成功 >
   首次创建/最近配置更新）。
5. 观测写入：started→running、结束 succeeded/failed、独立短事务与失败隔离、
   锁跳过零观测。
6. current_work：只读当前工作投影（kind/job_key/source_id/source_name/stage/
   started_at/item_id），只含运行中任务；不泄露信号标题、正文、异常文本与凭据。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.orm import Session

import app.scheduler.health as health_module
import app.scheduler.jobs as scheduler_jobs
import app.scheduler.runtime as scheduler_runtime
from app.agent.models import TycUsageRecord
from app.ai.models import AIAnalysisRecord
from app.auth import security as auth_security
from app.scheduler.health import build_monitoring_health
from app.scheduler.health_schemas import MonitoringHealthRead
from app.scheduler.observability_models import (
    JOB_RUN_STATUS_VALUES,
    SchedulerJobRegistry,
    SchedulerJobRun,
)
from app.scheduler.runtime import PENDING_SIGNALS_JOB_KEY, source_collection_job_key
from app.scheduler.runtime_models import (
    CURRENT_WORK_KIND_VALUES,
    CURRENT_WORK_STAGE_VALUES,
    RUNTIME_STATUS_VALUES,
    SchedulerRuntimeState,
)
from app.signals.models import CollectionRun, DataSource, RawSignal

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)  # 周四

# 天眼查周度分片 cadence "0 6 * * sun"（Asia/Shanghai = UTC+8）在 NOW 之后的
# 下一次触发：2026-09-13（周日）06:00 CST = 2026-09-12 22:00 UTC。
TYC_NEXT_WEEKLY_FIRE = datetime(2026, 9, 12, 22, 0, tzinfo=UTC)

REQUIRED_SOURCE_FIELDS = {
    "source_id",
    "last_success_at",
    "last_attempt_at",
    "next_expected_at",
    "state",
    "reason_code",
}


# --------------------------------------------------------------------------- #
# 共享构造
# --------------------------------------------------------------------------- #
def _make_source(
    session: Session,
    *,
    code: str,
    enabled: bool = True,
    schedule: str | None = None,
    adapter_status: str = "builtin",
    source_type: str = "api",
) -> DataSource:
    """普通测试源：code 不是真实适配器编码 → 不具备拉取能力（on_demand）。"""
    source = DataSource(
        code=code,
        name=f"信源-{code}",
        source_type=source_type,
        credibility=80,
        enabled=enabled,
        schedule=schedule,
        adapter_status=adapter_status,
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    session.add(source)
    session.flush()
    return source


def _pull_source(
    session: Session,
    *,
    code: str,
    schedule: str | None,
    enabled: bool = True,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
) -> DataSource:
    """真实拉取源（builtin 适配器编码可构建拉取适配器）；已存在则就地更新。"""
    source = session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
        source = DataSource(
            code=code,
            name=f"拉取源-{code}",
            source_type="official",
            credibility=80,
            enabled=enabled,
            schedule=schedule,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
        )
        session.add(source)
    else:
        source.enabled = enabled
        source.schedule = schedule
        source.adapter_status = "builtin"
    if created_at is not None:
        source.created_at = created_at
    if updated_at is not None:
        source.updated_at = updated_at
    session.flush()
    return source


def _tyc_source(
    session: Session,
    *,
    enabled: bool = True,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
    adapter_status: str = "builtin",
) -> DataSource:
    """真实的天眼查 external_tool 信源：测试库已有迁移种子行则就地启停。"""
    source = session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    if source is None:
        source = DataSource(
            code="tianyancha",
            name="天眼查企业核查",
            source_type="external_tool",
            credibility=90,
            enabled=enabled,
            schedule=None,
            adapter_status=adapter_status,
            adapter_version=0,
            auth_type="api_key",
            login_config={},
            adapter_config={},
        )
        session.add(source)
    else:
        source.enabled = enabled
        source.source_type = "external_tool"
        source.adapter_status = adapter_status
    if created_at is not None:
        source.created_at = created_at
    if updated_at is not None:
        source.updated_at = updated_at
    session.flush()
    return source


def _make_signal(
    session: Session,
    *,
    source: DataSource,
    fingerprint: str,
    collected_at: datetime,
    validity_state: str = "pending_classification",
    validity_reason_code: str | None = None,
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=fingerprint,
        title=f"信号 {fingerprint}",
        content="用于健康聚合口径校验。",
        fingerprint=fingerprint,
        raw_data={},
        collected_at=collected_at,
        validity_state=validity_state,
    )
    if validity_reason_code is not None:
        signal.validity_reason = {
            "code": validity_reason_code,
            "anchor_source": "collected_at",
            "details": {},
        }
    session.add(signal)
    session.flush()
    return signal


def _collection_run(
    session: Session, *, source: DataSource, status: str, started_at: datetime
) -> CollectionRun:
    run = CollectionRun(
        source_id=source.id,
        status=status,
        started_at=started_at,
        finished_at=started_at,
    )
    session.add(run)
    session.flush()
    return run


def _item_of(health: MonitoringHealthRead, code: str):
    for item in health.sources:
        if item.code == code:
            return item
    raise AssertionError(f"信源 {code} 不在健康聚合结果中")


def _disable_all_sources(session: Session) -> None:
    """整体测试库含迁移内置信源；四态 overall 用例先全部停用再精确启用。"""
    session.execute(update(DataSource).values(enabled=False))
    session.flush()


def _healthy_heartbeat(session: Session, *, at: datetime | None = None) -> None:
    session.add(
        SchedulerRuntimeState(
            job_key="scheduler", heartbeat_at=at or (NOW - timedelta(seconds=10))
        )
    )
    session.flush()


# --------------------------------------------------------------------------- #
# 1. 基线
# --------------------------------------------------------------------------- #
def test_baseline_system_health_contract_unchanged(client) -> None:
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_baseline_pending_candidate_predicate_excludes_disabled_and_terminal(
    db_session: Session,
) -> None:
    active_source = _make_source(db_session, code="baseline-active", schedule="*/30")
    disabled_source = _make_source(
        db_session, code="baseline-disabled", enabled=False, schedule="*/30"
    )
    _make_signal(
        db_session,
        source=active_source,
        fingerprint="baseline-keep",
        collected_at=NOW - timedelta(minutes=5),
    )
    _make_signal(
        db_session,
        source=disabled_source,
        fingerprint="baseline-drop-disabled",
        collected_at=NOW - timedelta(minutes=5),
    )
    _make_signal(
        db_session,
        source=active_source,
        fingerprint="baseline-drop-cf",
        collected_at=NOW - timedelta(minutes=5),
        validity_reason_code="classification_failed",
    )

    rows = db_session.execute(
        scheduler_jobs.pending_signal_candidate_select(now_utc=NOW)
    ).all()

    assert [code for _, code in rows] == ["baseline-active"]


def test_baseline_scheduler_registry_add_update_remove(monkeypatch) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    import app.scheduler.main as scheduler_main

    class _FakeSession:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self.rows = rows

        def __enter__(self) -> _FakeSession:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def scalars(self, query: object) -> list[SimpleNamespace]:
            del query
            return self.rows

    rows = [SimpleNamespace(id=71, code="reg", schedule="*/30 * * * *")]
    monkeypatch.setattr(scheduler_main, "SessionLocal", lambda: _FakeSession(rows))
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    scheduler_main._register_source_jobs(scheduler)
    assert scheduler.get_job("source-71") is not None
    original = str(scheduler.get_job("source-71").trigger)

    rows[0].schedule = "0 * * * *"
    scheduler_main._register_source_jobs(scheduler)
    assert str(scheduler.get_job("source-71").trigger) != original

    rows.clear()
    scheduler_main._register_source_jobs(scheduler)
    assert scheduler.get_job("source-71") is None


# --------------------------------------------------------------------------- #
# 2. 迁移与模型
# --------------------------------------------------------------------------- #
def test_runtime_table_columns_match_plan(db_session: Session) -> None:
    columns = {
        column["name"]
        for column in inspect(db_session.bind).get_columns("scheduler_runtime_state")
    }
    assert columns == {
        "job_key",
        "status",
        "last_started_at",
        "last_finished_at",
        "last_success_at",
        "heartbeat_at",
        "processed_count",
        "filtered_count",
        "failed_count",
        "error_code",
        "updated_at",
        "current_kind",
        "current_stage",
        "current_item_id",
    }
    assert RUNTIME_STATUS_VALUES == ("idle", "running", "succeeded", "failed")
    assert CURRENT_WORK_KIND_VALUES == ("source_collection", "pending_signal_processing")
    assert CURRENT_WORK_STAGE_VALUES == ("collecting", "processing_signal")


def test_migration_0049_declares_single_incremental_chain() -> None:
    """静态守卫：真实 0048→0049 增量与空库 head 两条路径由隔离迁移演练覆盖。"""
    migration_path = (
        Path(__file__).parent.parent / "alembic" / "versions" / "0049_scheduler_runtime.py"
    )
    text = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "0049"' in text
    assert 'down_revision: str | None = "0048"' in text
    plan_columns = (
        "last_started_at",
        "last_finished_at",
        "last_success_at",
        "heartbeat_at",
        "error_code",
    )
    for column in plan_columns:
        assert f'"{column}"' in text


def test_migration_0056_extends_runtime_state_for_current_work() -> None:
    """静态守卫：0056 紧接 0055，新增 current_work 三列（可空，向后兼容）。"""
    migration_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0056_scheduler_runtime_current_work.py"
    )
    text = migration_path.read_text(encoding="utf-8")
    assert 'revision: str = "0056"' in text
    assert 'down_revision: str | None = "0055"' in text
    for column in ("current_kind", "current_stage", "current_item_id"):
        assert f'"{column}"' in text


# --------------------------------------------------------------------------- #
# 3. scheduler 心跳段
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [(30, "ok"), (179, "ok"), (181, "stale")],
)
def test_scheduler_heartbeat_status_by_age(
    db_session: Session, age_seconds: int, expected: str
) -> None:
    _disable_all_sources(db_session)
    db_session.add(
        SchedulerRuntimeState(
            job_key="scheduler",
            heartbeat_at=NOW - timedelta(seconds=age_seconds),
        )
    )
    db_session.flush()
    health = build_monitoring_health(db_session, now=NOW)
    assert health.scheduler.status == expected
    assert health.scheduler.last_heartbeat_at == NOW - timedelta(seconds=age_seconds)
    assert health.scheduler.age_seconds == age_seconds
    assert health.scheduler.stale_after_seconds == 180
    assert health.scheduler.interval_seconds == 60


def test_scheduler_section_unknown_without_observation(db_session: Session) -> None:
    _disable_all_sources(db_session)
    health = build_monitoring_health(db_session, now=NOW)
    assert health.scheduler.status == "unknown"
    assert health.scheduler.last_heartbeat_at is None


# --------------------------------------------------------------------------- #
# 4. overall 四态真值表
# --------------------------------------------------------------------------- #
def test_overall_inactive_when_nothing_schedulable_and_no_pending(
    db_session: Session,
) -> None:
    _disable_all_sources(db_session)
    health = build_monitoring_health(db_session, now=NOW)
    assert health.overall == "inactive"


def test_overall_unknown_when_schedulable_without_heartbeat(
    db_session: Session,
) -> None:
    _disable_all_sources(db_session)
    _pull_source(
        db_session,
        code="nmc-weather",
        schedule="*/30 * * * *",
        updated_at=NOW - timedelta(minutes=5),
    )
    health = build_monitoring_health(db_session, now=NOW)
    assert health.overall == "unknown"


def test_overall_ok_with_fresh_heartbeat_and_no_failures(
    db_session: Session,
) -> None:
    _disable_all_sources(db_session)
    _pull_source(
        db_session,
        code="nmc-weather",
        schedule="0 21 * * *",
        updated_at=NOW - timedelta(minutes=10),
    )
    _healthy_heartbeat(db_session)
    health = build_monitoring_health(db_session, now=NOW)
    assert health.overall == "ok"


def test_overall_degraded_on_stale_heartbeat(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _pull_source(
        db_session,
        code="nmc-weather",
        schedule="*/30 * * * *",
        updated_at=NOW - timedelta(minutes=5),
    )
    _healthy_heartbeat(db_session, at=NOW - timedelta(seconds=181))
    assert build_monitoring_health(db_session, now=NOW).overall == "degraded"


def test_overall_degraded_on_failed_source(db_session: Session) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(
        db_session,
        code="ofac-sdn",
        schedule="*/30 * * * *",
        updated_at=NOW - timedelta(minutes=5),
    )
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="failed",
            last_finished_at=NOW - timedelta(minutes=5),
            error_code="collect_failed",
        )
    )
    _healthy_heartbeat(db_session)
    assert build_monitoring_health(db_session, now=NOW).overall == "degraded"


def test_overall_degraded_on_overdue_source(db_session: Session) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(
        db_session,
        code="nmc-weather",
        schedule="0 8 * * 1",
    )
    _collection_run(
        db_session,
        source=source,
        status="succeeded",
        started_at=NOW - timedelta(days=20),
    )
    _healthy_heartbeat(db_session)
    assert build_monitoring_health(db_session, now=NOW).overall == "degraded"


def test_overall_degraded_on_backlog_over_1h(db_session: Session) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(
        db_session,
        code="nmc-weather",
        schedule="0 21 * * *",
        updated_at=NOW - timedelta(minutes=5),
    )
    _make_signal(
        db_session,
        source=source,
        fingerprint="old-backlog",
        collected_at=NOW - timedelta(hours=2),
    )
    _healthy_heartbeat(db_session)
    health = build_monitoring_health(db_session, now=NOW)
    assert health.processing.total == 1
    assert health.processing.oldest_pending_age_seconds == 7200
    assert health.overall == "degraded"


def test_overall_degraded_on_last_run_failed(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _pull_source(
        db_session,
        code="nmc-weather",
        schedule="0 21 * * *",
        updated_at=NOW - timedelta(minutes=5),
    )
    db_session.add(
        SchedulerRuntimeState(
            job_key=PENDING_SIGNALS_JOB_KEY,
            status="failed",
            last_finished_at=NOW - timedelta(minutes=2),
            failed_count=1,
            error_code="pending_processing_failed",
        )
    )
    _healthy_heartbeat(db_session)
    health = build_monitoring_health(db_session, now=NOW)
    assert health.processing.last_run.status == "failed"
    assert health.overall == "degraded"


# --------------------------------------------------------------------------- #
# 5. 单源状态口径
# --------------------------------------------------------------------------- #
def test_source_disabled_and_on_demand(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _make_source(db_session, code="src-disabled", enabled=False, schedule="*/30 * * * *")
    _make_source(db_session, code="src-external", source_type="external_tool")
    # 恢复 conftest seed 的 manual-json 启用态：文件上传式，不因 enabled 而算可调度。
    _pull_source(db_session, code="manual-json", schedule=None)
    _healthy_heartbeat(db_session)

    health = build_monitoring_health(db_session, now=NOW)
    assert _item_of(health, "src-disabled").state == "disabled"
    assert _item_of(health, "src-external").state == "on_demand"
    # conftest 固定 seed 的 manual-json 是文件上传式，不因 enabled 而算可调度。
    assert _item_of(health, "manual-json").state == "on_demand"
    assert _item_of(health, "manual-json").next_expected_at is None


def test_source_invalid_schedule(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _pull_source(db_session, code="eu-compliance", schedule="not a cron")
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "eu-compliance")
    assert item.state == "invalid_schedule"
    assert item.reason_code == "invalid_schedule"
    assert item.next_expected_at is None


def test_null_schedule_pull_source_uses_scheduler_collect_cron(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="nmc-weather", schedule=None)
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="succeeded",
            last_success_at=NOW - timedelta(minutes=20),
            last_finished_at=NOW - timedelta(minutes=20),
        )
    )
    db_session.flush()
    # 兜底 cron 可由环境调整；monkeypatch 证明 NULL schedule 用的是它而非凭空 on_demand。
    monkeypatch.setattr(health_module, "SCHEDULER_COLLECT_CRON", "0 23 * * *")

    item = _item_of(build_monitoring_health(db_session, now=NOW), "nmc-weather")
    assert item.state == "ok"
    assert item.reason_code == "success_observed"
    # 0 23 * * *（Asia/Shanghai）= 15:00 UTC。
    assert item.next_expected_at == datetime(2026, 9, 10, 15, 0, tzinfo=UTC)
    assert item.last_success_at == NOW - timedelta(minutes=20)


@pytest.mark.parametrize(
    ("seconds_after_fire", "expected"),
    [(299, "ok"), (301, "overdue")],
)
def test_overdue_grace_300s_boundary(
    db_session: Session, seconds_after_fire: int, expected: str
) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="0 20 * * *")
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="succeeded",
            last_success_at=NOW - timedelta(minutes=10),
            last_finished_at=NOW - timedelta(minutes=10),
        )
    )
    db_session.flush()

    # 0 20 * * *（Asia/Shanghai）= 12:00 UTC；NOW 恰为其后 299/301 秒。
    now = NOW + timedelta(seconds=seconds_after_fire)
    item = _item_of(build_monitoring_health(db_session, now=now), "ofac-sdn")
    assert item.next_expected_at == NOW
    assert item.state == expected


def test_never_run_within_first_expected_window(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _pull_source(
        db_session,
        code="ofac-sdn",
        schedule="0 21 * * *",
        updated_at=NOW - timedelta(minutes=5),
    )
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "ofac-sdn")
    assert item.state == "never_run"
    assert item.reason_code == "never_run"
    assert item.last_success_at is None
    assert item.last_attempt_at is None
    # 0 21 * * *（Asia/Shanghai）= 13:00 UTC，晚于 NOW → 首个预期未过不算超期。
    assert item.next_expected_at == datetime(2026, 9, 10, 13, 0, tzinfo=UTC)


def test_never_run_beyond_config_anchor_becomes_overdue(db_session: Session) -> None:
    _disable_all_sources(db_session)
    anchor = NOW - timedelta(days=3)
    _pull_source(
        db_session,
        code="nmc-weather",
        schedule="0 * * * *",
        created_at=anchor,
        updated_at=anchor,
    )
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "nmc-weather")
    assert item.state == "overdue"
    assert item.reason_code == "missed_expected_trigger"
    assert item.last_success_at is None


def test_runtime_success_anchor_beats_cleaned_collection_run(
    db_session: Session,
) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="0 9 1 * *")
    # collection_runs 已被 30 天清理，仅剩 runtime 成功锚点 → 不应误报 overdue/never_run。
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="succeeded",
            last_success_at=NOW - timedelta(days=3),
            last_finished_at=NOW - timedelta(days=3),
        )
    )
    db_session.flush()

    item = _item_of(build_monitoring_health(db_session, now=NOW), "ofac-sdn")
    assert item.state == "ok"
    assert item.last_success_at == NOW - timedelta(days=3)


def test_latest_failure_after_success_marks_failed(db_session: Session) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(
        db_session,
        code="ofac-sdn",
        schedule="*/30 * * * *",
        updated_at=NOW - timedelta(minutes=30),
    )
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="succeeded",
            last_success_at=NOW - timedelta(hours=1),
            last_finished_at=NOW - timedelta(hours=1),
        )
    )
    _collection_run(
        db_session,
        source=source,
        status="failed",
        started_at=NOW - timedelta(minutes=5),
    )
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "ofac-sdn")
    assert item.state == "failed"
    assert item.reason_code == "last_attempt_failed"
    assert item.last_success_at == NOW - timedelta(hours=1)
    assert item.last_attempt_at == NOW - timedelta(minutes=5)


def test_newer_success_recovers_from_earlier_failure(db_session: Session) -> None:
    _disable_all_sources(db_session)
    source = _pull_source(
        db_session,
        code="nmc-weather",
        schedule="*/30 * * * *",
        updated_at=NOW - timedelta(minutes=30),
    )
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="failed",
            last_finished_at=NOW - timedelta(hours=1),
            error_code="collect_failed",
        )
    )
    _collection_run(
        db_session,
        source=source,
        status="succeeded",
        started_at=NOW - timedelta(minutes=10),
    )
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "nmc-weather")
    assert item.state == "ok"
    assert item.last_success_at == NOW - timedelta(minutes=10)
    assert item.last_attempt_at == NOW - timedelta(minutes=10)


def test_tyc_weekly_cadence_matches_registered_shard_jobs() -> None:
    """健康口径的周度 cadence 必须来自实际注册的天眼查分片 job，防止两处常量漂移。"""
    from apscheduler.schedulers.blocking import BlockingScheduler

    import app.scheduler.main as scheduler_main

    assert health_module.TYC_WEEKLY_SCHEDULE == scheduler_jobs.TYC_SHARD_CRONS[0]
    # main.py 注册 job 用的常量与 jobs.py 的健康 cadence 必须同源。
    assert scheduler_main.TYC_SHARD_CRONS == scheduler_jobs.TYC_SHARD_CRONS

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler_main._register_tyc_shard_jobs(scheduler)
    shard0 = scheduler.get_job("tyc-shard-0")
    assert shard0 is not None
    # 健康聚合使用的 cadence 与实际注册的 shard 0 触发点完全一致。
    assert str(shard0.trigger) == str(
        scheduler_main._trigger(health_module.TYC_WEEKLY_SCHEDULE)
    )


def test_tianyancha_enabled_uses_weekly_shard_schedule(
    db_session: Session,
) -> None:
    """启用的天眼查按权威周度分片 cadence 分类，而非按需/通用 cron 口径。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    _tyc_source(db_session, enabled=True)
    db_session.add_all(
        [
            TycUsageRecord(
                tool_name="verify_company",
                company_name="甲公司",
                status="success",
                called_at=NOW - timedelta(days=2),
            ),
            TycUsageRecord(
                tool_name="verify_company",
                company_name="乙公司",
                status="success",
                called_at=NOW - timedelta(minutes=30),
            ),
            TycUsageRecord(
                tool_name="verify_company",
                company_name="丙公司",
                status="error",
                called_at=NOW - timedelta(minutes=1),
            ),
        ]
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    # 启用后按周度分片口径分类：成功锚点取记账表最近 success，下一预期为下一个周日 06:00 CST。
    assert item.state == "ok"
    assert item.reason_code == "success_observed"
    assert item.next_expected_at == TYC_NEXT_WEEKLY_FIRE
    # 最新一条是 error，不得冒充成功；取最近 success。
    assert item.last_success_at == NOW - timedelta(minutes=30)


def test_tianyancha_within_weekly_shard_period_is_not_overdue(
    db_session: Session,
) -> None:
    """分片期内（最近成功在周度窗口内）不得误报 stale/overdue。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    _tyc_source(db_session, enabled=True)
    # 最近一次成功在 2 天前（周二），下一预期仍在下个周日 → 尚在分片周期内。
    db_session.add(
        TycUsageRecord(
            tool_name="verify_company",
            company_name="戊公司",
            status="success",
            called_at=NOW - timedelta(days=2),
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    assert item.state == "ok"
    assert item.next_expected_at == TYC_NEXT_WEEKLY_FIRE


def test_tianyancha_beyond_weekly_window_becomes_overdue(
    db_session: Session,
) -> None:
    """越过整个周度窗口仍无成功 → overdue（真超期）。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    _tyc_source(db_session, enabled=True)
    # 最近成功在 8 天前，跨过了上一个周日分片日 → 已超期。
    db_session.add(
        TycUsageRecord(
            tool_name="verify_company",
            company_name="己公司",
            status="success",
            called_at=NOW - timedelta(days=8),
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    assert item.state == "overdue"
    assert item.reason_code == "missed_expected_trigger"


def test_tianyancha_without_successful_usage_has_no_last_success(
    db_session: Session,
) -> None:
    """无 success 记账时不得凭空推导成功时间；启用且未越过首个预期 → never_run。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    # 配置锚点取 1 天前：下一个周日尚未到 → never_run，而非 overdue。
    _tyc_source(
        db_session,
        enabled=True,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )
    db_session.add(
        TycUsageRecord(
            tool_name="verify_company",
            company_name="丁公司",
            status="error",
            called_at=NOW - timedelta(minutes=5),
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    # 仅有一次失败记账、从无成功：不得标 failed，也不得编造成功时间。
    assert item.state == "never_run"
    assert item.reason_code == "never_run"
    assert item.last_success_at is None
    assert item.next_expected_at == TYC_NEXT_WEEKLY_FIRE


def test_tianyancha_never_run_beyond_first_window_is_overdue(
    db_session: Session,
) -> None:
    """启用后越过首个周度预期仍无成功 → overdue。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    _tyc_source(
        db_session,
        enabled=True,
        created_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(days=10),
    )
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    assert item.state == "overdue"
    assert item.reason_code == "missed_expected_trigger"
    assert item.last_success_at is None


def test_tianyancha_disabled_stays_disabled(db_session: Session) -> None:
    """停用的外部核查来源仍归 disabled，不因周度 cadence 被算成待调度。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    _tyc_source(db_session, enabled=False)
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    assert item.state == "disabled"
    assert item.reason_code == "disabled"
    assert item.next_expected_at is None
    assert item.last_success_at is None


def test_tianyancha_enabled_unconfigured_adapter_is_not_disabled(
    db_session: Session,
) -> None:
    """已启用的外部核查源不因 adapter_status 停留在 unconfigured 被误报停用。"""
    db_session.execute(delete(TycUsageRecord))
    _disable_all_sources(db_session)
    _tyc_source(db_session, enabled=True, adapter_status="unconfigured")
    db_session.add(
        TycUsageRecord(
            tool_name="verify_company",
            company_name="庚公司",
            status="success",
            called_at=NOW - timedelta(minutes=30),
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "tianyancha")
    assert item.state == "ok"
    assert item.reason_code == "success_observed"
    assert item.next_expected_at == TYC_NEXT_WEEKLY_FIRE

    health = build_monitoring_health(db_session, now=NOW)
    # 前置条件：唯一可调度来源成功且无积压，overall 才可能为 ok。
    assert health.processing.total == 0
    assert health.overall == "ok"


def test_enabled_external_tool_unconfigured_is_on_demand(db_session: Session) -> None:
    """启用的非天眼查外部核查工具（无调度）归 on_demand，而非 disabled。"""
    _disable_all_sources(db_session)
    _make_source(
        db_session,
        code="ext-tool-a",
        source_type="external_tool",
        schedule=None,
        adapter_status="unconfigured",
    )
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "ext-tool-a")
    assert item.state == "on_demand"


# --------------------------------------------------------------------------- #
# 6. pending 口径
# --------------------------------------------------------------------------- #
def test_pending_counts_backlog_and_classification_failed_separately(
    db_session: Session,
) -> None:
    _disable_all_sources(db_session)
    source = _make_source(db_session, code="src-pending", schedule="*/30 * * * *")
    _make_signal(
        db_session,
        source=source,
        fingerprint="fresh",
        collected_at=NOW - timedelta(minutes=10),
    )
    _make_signal(
        db_session,
        source=source,
        fingerprint="stale-backlog",
        collected_at=NOW - timedelta(hours=5),
    )
    _make_signal(
        db_session,
        source=source,
        fingerprint="cf",
        collected_at=NOW - timedelta(hours=5),
        validity_reason_code="classification_failed",
    )

    pending = build_monitoring_health(db_session, now=NOW).processing
    assert pending.total == 2  # classification_failed 不计入候选
    assert pending.backlog_over_1h == 1
    assert pending.classification_failed == 1
    assert pending.oldest_pending_age_seconds == int(timedelta(hours=5).total_seconds())


def test_pending_last_run_zero_new_success(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _healthy_heartbeat(db_session)
    db_session.add(
        SchedulerRuntimeState(
            job_key=PENDING_SIGNALS_JOB_KEY,
            status="succeeded",
            last_finished_at=NOW - timedelta(minutes=2),
            last_success_at=NOW - timedelta(minutes=2),
            processed_count=0,
            filtered_count=0,
            failed_count=0,
        )
    )
    db_session.flush()

    last_run = build_monitoring_health(db_session, now=NOW).processing.last_run
    assert last_run.status == "succeeded"
    assert (last_run.processed, last_run.filtered, last_run.failed) == (0, 0, 0)


# --------------------------------------------------------------------------- #
# 7. 观测写入语义
# --------------------------------------------------------------------------- #
@pytest.fixture
def runtime_uses_test_session(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    yield


def test_record_heartbeat_upserts_single_row(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    assert scheduler_runtime.record_heartbeat(now=NOW) is True
    assert scheduler_runtime.record_heartbeat(now=NOW + timedelta(seconds=60)) is True

    rows = list(db_session.scalars(select(SchedulerRuntimeState)))
    assert len(rows) == 1
    assert rows[0].job_key == "scheduler"
    assert rows[0].heartbeat_at == NOW + timedelta(seconds=60)


def test_record_job_started_marks_running(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    assert (
        scheduler_runtime.record_job_started(
            PENDING_SIGNALS_JOB_KEY,
            kind="pending_signal_processing",
            stage="processing_signal",
            now=NOW,
        )
        is True
    )

    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.status == "running"
    assert row.last_started_at == NOW
    assert row.current_kind == "pending_signal_processing"
    assert row.current_stage == "processing_signal"
    assert row.current_item_id is None


def test_record_job_started_overwrites_stale_current_work(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    """开始新任务必须覆盖上一轮残留：item_id 缺省即清空，不继承旧观测。"""
    scheduler_runtime.record_job_started(
        PENDING_SIGNALS_JOB_KEY,
        kind="pending_signal_processing",
        stage="processing_signal",
        item_id=41,
        now=NOW,
    )
    scheduler_runtime.record_job_started(
        PENDING_SIGNALS_JOB_KEY,
        kind="pending_signal_processing",
        stage="processing_signal",
        now=NOW + timedelta(seconds=60),
    )

    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.current_item_id is None


def test_record_pending_signal_started_only_refreshes_current_item(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    """单信号埋点只刷新当前项；批次级 last_started_at 不被逐信号刷新。"""
    scheduler_runtime.record_job_started(
        PENDING_SIGNALS_JOB_KEY,
        kind="pending_signal_processing",
        stage="processing_signal",
        now=NOW,
    )
    assert (
        scheduler_runtime.record_pending_signal_started(
            41, now=NOW + timedelta(seconds=5)
        )
        is True
    )

    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.current_kind == "pending_signal_processing"
    assert row.current_stage == "processing_signal"
    assert row.current_item_id == 41
    assert row.last_started_at == NOW


def test_record_job_result_and_neutral_clear_current_work(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    """结束结论（failed / 中性）必须清空 current_*，不残留“运行中”信息。"""
    key = source_collection_job_key(7)
    scheduler_runtime.record_job_started(
        key, kind="source_collection", stage="collecting", now=NOW
    )
    scheduler_runtime.record_job_result(key, succeeded=False, now=NOW)
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(SchedulerRuntimeState.job_key == key)
    )
    assert row is not None
    assert row.status == "failed"
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)

    scheduler_runtime.record_job_started(
        key, kind="source_collection", stage="collecting", now=NOW
    )
    scheduler_runtime.record_job_neutral(key, now=NOW)
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(SchedulerRuntimeState.job_key == key)
    )
    assert row is not None
    assert row.status == "idle"
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)


def test_record_job_result_persists_only_stable_error_code(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    scheduler_runtime.record_job_result(
        PENDING_SIGNALS_JOB_KEY,
        succeeded=False,
        failed=1,
        error_code="Traceback: psycopg.OperationalError at 10.0.0.5:5432",
        now=NOW,
    )
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == "unknown_error"
    assert row.last_success_at is None


def test_record_job_result_success_updates_last_success(
    db_session: Session, runtime_uses_test_session: None
) -> None:
    scheduler_runtime.record_job_result(
        PENDING_SIGNALS_JOB_KEY, succeeded=True, processed=2, now=NOW
    )
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.status == "succeeded"
    assert row.last_success_at == NOW
    assert row.error_code is None
    assert row.processed_count == 2


def test_observation_write_failure_does_not_raise(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> nullcontext[Session]:
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler_runtime, "SessionLocal", _boom)
    assert scheduler_runtime.record_heartbeat(now=NOW) is False
    assert (
        scheduler_runtime.record_job_result(PENDING_SIGNALS_JOB_KEY, succeeded=True, now=NOW)
        is False
    )
    assert (
        scheduler_runtime.record_job_started(
            PENDING_SIGNALS_JOB_KEY,
            kind="pending_signal_processing",
            stage="processing_signal",
            now=NOW,
        )
        is False
    )


def test_pending_processing_lock_skip_writes_no_observation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    assert scheduler_jobs._pending_signal_processing_lock.acquire(blocking=False)
    try:
        result = scheduler_jobs._process_pending_signals(limit=5, now_utc=NOW)
    finally:
        scheduler_jobs._pending_signal_processing_lock.release()

    assert result == 0
    assert db_session.scalar(select(SchedulerRuntimeState)) is None


def test_pending_processing_writes_started_then_succeeded(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    scheduler_jobs._process_pending_signals(limit=5, now_utc=NOW)

    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.status == "succeeded"
    assert row.last_started_at is not None
    assert row.last_finished_at is not None
    assert row.last_success_at is not None
    assert row.error_code is None
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)


def _fake_run(count: int) -> SimpleNamespace:
    return SimpleNamespace(
        created_count=count, fetched_count=count + 3, duplicate_count=1
    )


def test_collect_source_writes_started_and_succeeded(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_jobs, "collect_source", lambda session, src, adapter: _fake_run(2)
    )
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="nmc-weather", schedule="*/30 * * * *")

    summary = scheduler_jobs._collect_enabled_sources(source_ids=[source.id])

    assert summary == {"nmc-weather": 2}
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == source_collection_job_key(source.id)
        )
    )
    assert row is not None
    assert row.status == "succeeded"
    assert row.last_started_at is not None
    assert row.last_finished_at is not None
    assert row.last_success_at is not None
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)


def test_collect_source_failure_records_failed_observation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.signals.service import CollectionFailed

    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )

    def _boom(session: object, source: object, adapter: object) -> object:
        raise CollectionFailed("网络不可达（测试注入）")

    monkeypatch.setattr(scheduler_jobs, "collect_source", _boom)
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="*/30 * * * *")

    summary = scheduler_jobs._collect_enabled_sources(source_ids=[source.id])

    assert summary == {"ofac-sdn": -1}
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == source_collection_job_key(source.id)
        )
    )
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == "collect_failed"
    assert row.last_success_at is None


def test_collect_source_deferred_records_neutral_observation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受控延后：中性完成（清空当前状态/错误），保留 last_success_at，不写失败观测。"""
    from app.signals.service import CollectionDeferred

    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="*/30 * * * *")
    scheduler_runtime.record_source_collection(
        source.id, succeeded=True, now=NOW - timedelta(hours=1)
    )

    def _deferred(session: object, src: object, adapter: object) -> object:
        del session, src, adapter
        raise CollectionDeferred("信息源域名冷却中")

    monkeypatch.setattr(scheduler_jobs, "collect_source", _deferred)

    summary = scheduler_jobs._collect_enabled_sources(source_ids=[source.id])

    assert summary == {}
    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == source_collection_job_key(source.id)
        )
    )
    assert row is not None
    assert row.status == "idle"
    assert row.error_code is None
    assert row.last_finished_at is not None
    assert row.last_success_at == NOW - timedelta(hours=1)
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)


def test_runtime_success_anchor_survives_neutral_completion(db_session: Session) -> None:
    """健康成功锚点只认已持久化的 last_success_at，与当前状态无关。"""
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="0 9 1 * *")
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="idle",
            last_success_at=NOW - timedelta(days=3),
            last_finished_at=NOW - timedelta(days=3),
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    item = _item_of(build_monitoring_health(db_session, now=NOW), "ofac-sdn")
    assert item.state == "ok"
    assert item.last_success_at == NOW - timedelta(days=3)


# --------------------------------------------------------------------------- #
# 8. HTTP 契约与权限
# --------------------------------------------------------------------------- #
def test_monitoring_health_http_contract_fields(client, db_session: Session) -> None:
    real_now = datetime.now(UTC)
    _disable_all_sources(db_session)
    source = _pull_source(
        db_session,
        code="ofac-sdn",
        schedule="*/30 * * * *",
        updated_at=real_now - timedelta(minutes=5),
    )
    source.endpoint_url = "https://secret.internal.example/collect"
    source.credential_ref = "env:SECRET_TOKEN"
    _healthy_heartbeat(db_session, at=real_now - timedelta(seconds=10))

    response = client.get("/api/v1/system/monitoring-health")
    assert response.status_code == 200
    body = response.json()
    # 计划契约顶层字段；本轮未发布，不保留旧字段兼容层。
    assert set(body) == {"as_of", "overall", "scheduler", "processing", "sources"}
    assert body["overall"] == "ok"
    assert set(body["scheduler"]) >= {"status", "last_heartbeat_at", "stale_after_seconds"}
    assert set(body["processing"]) >= {"total", "classification_failed", "last_run"}
    assert len(body["sources"]) >= 1
    for item in body["sources"]:
        assert REQUIRED_SOURCE_FIELDS <= set(item)
    target = next(item for item in body["sources"] if item["code"] == "ofac-sdn")
    assert target["state"] == "never_run"
    assert target["next_expected_at"] is not None
    assert "secret.internal.example" not in response.text
    assert "SECRET_TOKEN" not in response.text


def test_monitoring_health_requires_session(client) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    response = client.get("/api/v1/system/monitoring-health")
    assert response.status_code == 401


def test_monitoring_health_requires_permission(client, monkeypatch) -> None:
    patched = {
        role: (perms - {"source_status_view"})
        for role, perms in auth_security.ROLE_PERMISSIONS.items()
    }
    monkeypatch.setattr(auth_security, "ROLE_PERMISSIONS", patched)
    response = client.get("/api/v1/system/monitoring-health")
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 9. current_work：调度器当前工作观测
# --------------------------------------------------------------------------- #
def _running_current_row(
    *,
    job_key: str,
    kind: str,
    stage: str,
    item_id: int | None = None,
    started_at: datetime | None = None,
) -> SchedulerRuntimeState:
    return SchedulerRuntimeState(
        job_key=job_key,
        status="running",
        last_started_at=started_at or NOW,
        current_kind=kind,
        current_stage=stage,
        current_item_id=item_id,
    )


def test_current_work_lists_running_collection(db_session: Session) -> None:
    """运行中的 collect:<source_id> 映射为信源采集，并带信源名称。"""
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="*/30 * * * *")
    db_session.add(
        _running_current_row(
            job_key=source_collection_job_key(source.id),
            kind="source_collection",
            stage="collecting",
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    current = build_monitoring_health(db_session, now=NOW).scheduler.current_work
    assert len(current) == 1
    item = current[0]
    assert item.kind == "source_collection"
    assert item.job_key == source_collection_job_key(source.id)
    assert item.source_id == source.id
    assert item.source_name == source.name
    assert item.stage == "collecting"
    assert item.started_at == NOW
    assert item.item_id is None


def test_current_work_lists_running_pending_signal_item(db_session: Session) -> None:
    """运行中的 pending_signals 映射为待处理信号，item_id 是当前信号 id。"""
    _disable_all_sources(db_session)
    db_session.add(
        _running_current_row(
            job_key=PENDING_SIGNALS_JOB_KEY,
            kind="pending_signal_processing",
            stage="processing_signal",
            item_id=42,
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    current = build_monitoring_health(db_session, now=NOW).scheduler.current_work
    assert len(current) == 1
    item = current[0]
    assert item.kind == "pending_signal_processing"
    assert item.job_key == PENDING_SIGNALS_JOB_KEY
    assert item.source_id is None
    assert item.source_name is None
    assert item.stage == "processing_signal"
    assert item.started_at == NOW
    assert item.item_id == 42


def test_current_work_excludes_finished_unlabeled_and_unknown_rows(
    db_session: Session,
) -> None:
    """只包含运行中且可识别的任务；结束行、未登记行、未知 job_key 与缺信源行全部省略。"""
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="*/30 * * * *")
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="succeeded",
            last_started_at=NOW - timedelta(minutes=5),
            current_kind="source_collection",
            current_stage="collecting",
        )
    )
    db_session.add(
        SchedulerRuntimeState(job_key=PENDING_SIGNALS_JOB_KEY, status="running")
    )
    db_session.add(
        _running_current_row(
            job_key="mystery-job", kind="source_collection", stage="collecting"
        )
    )
    db_session.add(
        _running_current_row(
            job_key="collect:987654321", kind="source_collection", stage="collecting"
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session)

    assert build_monitoring_health(db_session, now=NOW).scheduler.current_work == []


def test_pending_processing_records_current_item_before_each_signal(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """处理每个信号前写入 item_id/stage；处理期间可观察，批次结束后清空。"""
    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    _disable_all_sources(db_session)
    source = _make_source(db_session, code="src-current-work", schedule="*/30 * * * *")
    signal = _make_signal(
        db_session,
        source=source,
        fingerprint="current-work",
        collected_at=NOW - timedelta(minutes=30),
    )
    db_session.add(
        AIAnalysisRecord(
            signal_id=signal.id,
            provider="current-work-test",
            model="current-work-v1",
            prompt_version="current-work-v1",
            status="succeeded",
            result={},
        )
    )
    db_session.flush()

    observed: list[tuple[int | None, str | None]] = []

    def spy_process_analysis(
        session: Session,
        current: RawSignal,
        analysis: AIAnalysisRecord,
        *,
        now_utc: datetime,
    ) -> None:
        del session, current, analysis, now_utc
        row = db_session.scalar(
            select(SchedulerRuntimeState).where(
                SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
            )
        )
        assert row is not None
        observed.append((row.current_item_id, row.current_stage))

    monkeypatch.setattr(scheduler_jobs, "process_analysis", spy_process_analysis)

    assert scheduler_jobs._process_pending_signals(limit=5, now_utc=NOW) == 1
    assert observed == [(signal.id, "processing_signal")]

    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == PENDING_SIGNALS_JOB_KEY
        )
    )
    assert row is not None
    assert row.status == "succeeded"
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)


def test_collect_source_current_work_visible_during_collection_and_cleared_after(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """采集期间 current_work 显示该信源的采集任务；采集结束后清理。"""
    monkeypatch.setattr(
        scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        scheduler_runtime, "SessionLocal", lambda: nullcontext(db_session)
    )
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="nmc-weather", schedule="*/30 * * * *")
    observed: list[tuple[str | None, str | None]] = []

    def fake_collect(session: object, src: object, adapter: object) -> object:
        del session, src, adapter
        row = db_session.scalar(
            select(SchedulerRuntimeState).where(
                SchedulerRuntimeState.job_key == source_collection_job_key(source.id)
            )
        )
        assert row is not None
        observed.append((row.current_kind, row.current_stage))
        return _fake_run(2)

    monkeypatch.setattr(scheduler_jobs, "collect_source", fake_collect)

    assert scheduler_jobs._collect_enabled_sources(source_ids=[source.id]) == {
        "nmc-weather": 2
    }
    assert observed == [("source_collection", "collecting")]

    row = db_session.scalar(
        select(SchedulerRuntimeState).where(
            SchedulerRuntimeState.job_key == source_collection_job_key(source.id)
        )
    )
    assert row is not None
    assert row.status == "succeeded"
    assert (row.current_kind, row.current_stage, row.current_item_id) == (None, None, None)


def test_monitoring_health_http_current_work_contract(
    client, db_session: Session
) -> None:
    """HTTP 契约：current_work 只含 7 个白名单字段，不泄露标题、正文与凭据。"""
    real_now = datetime.now(UTC)
    _disable_all_sources(db_session)
    source = _pull_source(db_session, code="ofac-sdn", schedule="*/30 * * * *")
    source.endpoint_url = "https://secret.internal.example/collect"
    source.credential_ref = "env:SECRET_TOKEN"
    signal = _make_signal(
        db_session,
        source=source,
        fingerprint="current-work-secret",
        collected_at=real_now - timedelta(minutes=5),
    )
    db_session.add(
        SchedulerRuntimeState(
            job_key=source_collection_job_key(source.id),
            status="running",
            last_started_at=real_now - timedelta(seconds=5),
            current_kind="source_collection",
            current_stage="collecting",
        )
    )
    db_session.add(
        SchedulerRuntimeState(
            job_key=PENDING_SIGNALS_JOB_KEY,
            status="running",
            last_started_at=real_now - timedelta(seconds=3),
            current_kind="pending_signal_processing",
            current_stage="processing_signal",
            current_item_id=signal.id,
        )
    )
    db_session.flush()
    _healthy_heartbeat(db_session, at=real_now - timedelta(seconds=10))

    response = client.get("/api/v1/system/monitoring-health")
    assert response.status_code == 200
    body = response.json()
    work = body["scheduler"]["current_work"]
    assert len(work) == 2
    by_kind = {item["kind"]: item for item in work}
    assert set(by_kind) == {"source_collection", "pending_signal_processing"}
    collection = by_kind["source_collection"]
    assert set(collection) == {
        "kind",
        "job_key",
        "source_id",
        "source_name",
        "stage",
        "started_at",
        "item_id",
    }
    assert collection["job_key"] == source_collection_job_key(source.id)
    assert collection["source_id"] == source.id
    assert collection["source_name"] == source.name
    assert collection["stage"] == "collecting"
    assert collection["item_id"] is None
    assert collection["started_at"] is not None
    pending = by_kind["pending_signal_processing"]
    # current_item_id 指向真实信号：投影该信号所属信源，而非批次级 null。
    assert pending["source_id"] == source.id
    assert pending["source_name"] == source.name
    assert pending["stage"] == "processing_signal"
    assert pending["item_id"] == signal.id
    # 不泄露信号标题/正文、采集地址与凭据引用。
    assert signal.title not in response.text
    assert signal.content not in response.text
    assert "secret.internal.example" not in response.text
    assert "SECRET_TOKEN" not in response.text


# --------------------------------------------------------------------------- #
# 10. scheduler 排期快照与执行历史（0057）
# --------------------------------------------------------------------------- #
def _clear_observability_rows(session: Session) -> None:
    session.execute(delete(SchedulerJobRun))
    session.execute(delete(SchedulerJobRegistry))
    session.flush()


def test_observability_tables_match_contract(db_session: Session) -> None:
    registry_columns = {
        column["name"]
        for column in inspect(db_session.bind).get_columns("scheduler_job_registry")
    }
    assert registry_columns == {"job_id", "name", "next_run_at", "updated_at"}

    run_columns = {
        column["name"]
        for column in inspect(db_session.bind).get_columns("scheduler_job_run")
    }
    assert run_columns == {
        "id",
        "job_id",
        "name",
        "status",
        "scheduled_run_at",
        "started_at",
        "finished_at",
        "created_at",
    }
    assert JOB_RUN_STATUS_VALUES == (
        "running",
        "completed",
        "error",
        "missed",
        "max_instances",
    )
    # 事件幂等键由唯一约束兜底并发。
    unique = {
        tuple(constraint["column_names"])
        for constraint in inspect(db_session.bind).get_unique_constraints("scheduler_job_run")
    }
    assert ("job_id", "scheduled_run_at") in unique


def test_scheduled_jobs_sorted_by_next_run_at_with_null_last(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _clear_observability_rows(db_session)
    db_session.add_all(
        [
            SchedulerJobRegistry(
                job_id="collect", name="定时采集与处理", next_run_at=NOW + timedelta(minutes=5)
            ),
            SchedulerJobRegistry(
                job_id="source-7",
                name="采集信息源 ofac-sdn",
                next_run_at=NOW + timedelta(minutes=1),
            ),
            SchedulerJobRegistry(job_id="notify", name="风险提醒推送", next_run_at=None),
        ]
    )
    db_session.flush()

    scheduled = build_monitoring_health(db_session, now=NOW).scheduler.scheduled_jobs
    assert [item.job_id for item in scheduled] == ["source-7", "collect", "notify"]
    assert scheduled[0].next_run_at == NOW + timedelta(minutes=1)
    assert scheduled[0].name == "采集信息源 ofac-sdn"
    # 无下次触发时间的 job 排在最后且如实为 null，Web 不推算。
    assert scheduled[2].next_run_at is None


def test_recent_runs_limited_to_latest_30_desc(db_session: Session) -> None:
    _disable_all_sources(db_session)
    _clear_observability_rows(db_session)
    for index in range(33):
        db_session.add(
            SchedulerJobRun(
                job_id="collect",
                name="定时采集与处理",
                status="completed",
                scheduled_run_at=NOW - timedelta(minutes=index),
                started_at=NOW - timedelta(minutes=index),
                finished_at=NOW - timedelta(minutes=index),
            )
        )
    db_session.flush()

    recent = build_monitoring_health(db_session, now=NOW).scheduler.recent_runs
    assert len(recent) == 30
    assert [item.scheduled_run_at for item in recent] == [
        NOW - timedelta(minutes=index) for index in range(30)
    ]
    assert all(item.job_id == "collect" for item in recent)


def test_monitoring_health_http_lists_scheduled_jobs_and_recent_runs(
    client, db_session: Session
) -> None:
    real_now = datetime.now(UTC)
    _disable_all_sources(db_session)
    _clear_observability_rows(db_session)
    db_session.add(
        SchedulerJobRegistry(
            job_id="collect",
            name="定时采集与处理",
            next_run_at=real_now + timedelta(minutes=3),
        )
    )
    db_session.add(
        SchedulerJobRun(
            job_id="collect",
            name="定时采集与处理",
            status="completed",
            scheduled_run_at=real_now - timedelta(minutes=2),
            started_at=real_now - timedelta(minutes=2),
            finished_at=real_now - timedelta(minutes=1),
        )
    )
    _healthy_heartbeat(db_session, at=real_now - timedelta(seconds=10))
    db_session.flush()

    response = client.get("/api/v1/system/monitoring-health")
    assert response.status_code == 200
    body = response.json()

    scheduled = body["scheduler"]["scheduled_jobs"]
    assert len(scheduled) == 1
    assert set(scheduled[0]) == {"job_id", "name", "next_run_at"}
    assert scheduled[0]["job_id"] == "collect"
    assert scheduled[0]["name"] == "定时采集与处理"
    assert scheduled[0]["next_run_at"] is not None

    runs = body["scheduler"]["recent_runs"]
    assert len(runs) == 1
    assert set(runs[0]) == {
        "id",
        "job_id",
        "name",
        "status",
        "scheduled_run_at",
        "started_at",
        "finished_at",
    }
    assert runs[0]["job_id"] == "collect"
    assert runs[0]["name"] == "定时采集与处理"
    assert runs[0]["status"] == "completed"
    assert runs[0]["scheduled_run_at"] is not None
    assert runs[0]["started_at"] is not None
    assert runs[0]["finished_at"] is not None
    # completed 只表示 callable 正常返回；绝不出现 succeeded 命名。
    assert "succeeded" not in response.text


def test_monitoring_health_http_observability_arrays_empty_without_data(
    client, db_session: Session
) -> None:
    real_now = datetime.now(UTC)
    _disable_all_sources(db_session)
    _clear_observability_rows(db_session)
    _healthy_heartbeat(db_session, at=real_now - timedelta(seconds=10))

    response = client.get("/api/v1/system/monitoring-health")
    assert response.status_code == 200
    body = response.json()
    assert body["scheduler"]["scheduled_jobs"] == []
    assert body["scheduler"]["recent_runs"] == []
