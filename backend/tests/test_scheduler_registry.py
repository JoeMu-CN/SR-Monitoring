"""信息源调度注册表动态刷新测试。"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from typing import NoReturn
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import delete, select

import app.research.schedule as research_schedule
import app.scheduler.jobs as scheduler_jobs
import app.scheduler.main as scheduler_main
import app.scheduler.observability as scheduler_observability
from app.ai.models import AIAnalysisRecord
from app.auth.models import User
from app.config import SearchSettings
from app.research.models import ResearchBatch, ResearchTask
from app.scheduler.observability import JobLike
from app.scheduler.observability_models import SchedulerJobRegistry
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.service import STALE_COLLECTION_RUN_ERROR
from app.suppliers.models import Supplier


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


def test_source_schedule_registry_adds_updates_and_removes(monkeypatch) -> None:
    rows = [SimpleNamespace(id=91, code="dynamic-source", schedule="*/30 * * * *")]
    monkeypatch.setattr(scheduler_main, "SessionLocal", lambda: _FakeSession(rows))
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    scheduler_main._register_source_jobs(scheduler)
    job = scheduler.get_job("source-91")
    assert job is not None
    original_trigger = str(job.trigger)

    rows[0].schedule = "0 * * * *"
    scheduler_main._register_source_jobs(scheduler)
    assert str(scheduler.get_job("source-91").trigger) != original_trigger  # type: ignore[union-attr]

    rows.clear()
    scheduler_main._register_source_jobs(scheduler)
    assert scheduler.get_job("source-91") is None


def test_scheduler_startup_finalizes_stale_collection_runs(db_session, monkeypatch) -> None:
    """Scheduler 启动时一次性收尾上次异常退出遗留的 running 运行。

    超过 30 分钟阈值的 running 收尾为 failed；阈值内的新鲜 running 保持不动，
    避免把仍在执行的长任务误判为陈旧。
    """
    source = DataSource(
        code="startup-recovery-source",
        name="启动恢复测试信源",
        source_type="api",
        credibility=80,
        enabled=True,
    )
    db_session.add(source)
    db_session.flush()
    now = datetime.now(UTC)
    stale = CollectionRun(
        source_id=source.id,
        status="running",
        started_at=now - timedelta(minutes=45),
    )
    fresh = CollectionRun(
        source_id=source.id,
        status="running",
        started_at=now - timedelta(minutes=5),
    )
    db_session.add_all([stale, fresh])
    db_session.commit()
    stale_id, fresh_id = stale.id, fresh.id
    monkeypatch.setattr(scheduler_main, "SessionLocal", lambda: nullcontext(db_session))

    finalized = scheduler_main._finalize_stale_collection_runs_on_startup()

    assert finalized == 1
    stale_run = db_session.get(CollectionRun, stale_id)
    fresh_run = db_session.get(CollectionRun, fresh_id)
    assert stale_run is not None and fresh_run is not None
    assert stale_run.status == "failed"
    assert stale_run.finished_at is not None
    assert stale_run.error == STALE_COLLECTION_RUN_ERROR
    assert fresh_run.status == "running"
    assert fresh_run.finished_at is None
    assert fresh_run.error is None


def test_daily_research_job_is_idempotent_and_only_creates_task(db_session, monkeypatch) -> None:
    owner = User(
        username="scheduled-research-admin",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_DAILY_TOPIC", "每日供应链风险摘要")
    now = datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

    first = scheduler_jobs.create_research_task_job("daily", now=now)
    second = scheduler_jobs.create_research_task_job("daily", now=now)

    tasks = list(db_session.scalars(select(ResearchTask)))
    assert first == second
    assert len(tasks) == 1
    assert tasks[0].task_type == "daily"
    assert tasks[0].status == "queued"
    assert tasks[0].idempotency_key == "scheduled:daily:2026-08-12"
    assert tasks[0].search_queries_used == 0


def test_research_job_skips_when_schedule_configuration_is_incomplete(
    monkeypatch,
) -> None:
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_DAILY_TOPIC", "")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", "research-admin")

    assert scheduler_jobs.create_research_task_job("daily") is None


def test_monthly_research_batch_fans_out_enabled_suppliers_once(db_session, monkeypatch) -> None:
    owner = User(
        username="monthly-research-admin",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.add_all(
        [
            Supplier(supplier_code="MONTHLY-001", legal_name="启用供应商一", country_code="CN"),
            Supplier(supplier_code="MONTHLY-002", legal_name="启用供应商二", country_code="CN"),
            Supplier(
                supplier_code="MONTHLY-DISABLED",
                legal_name="停用供应商",
                country_code="CN",
                enabled=False,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_ENABLED", True)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_CRON", "0 9 1 * *")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_TOPIC", "全供应商月度风险")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_ORCHESTRATOR", "langgraph")
    monkeypatch.setattr(
        research_schedule,
        "get_search_settings",
        lambda: SearchSettings("bocha", "configured-for-test", "", 15, 2_000),
    )
    now = datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

    first = scheduler_jobs.create_monthly_research_batch_job(now=now)
    second = scheduler_jobs.create_monthly_research_batch_job(now=now)

    batch = db_session.scalar(select(ResearchBatch).where(ResearchBatch.id == first))
    tasks = list(
        db_session.scalars(
            select(ResearchTask).where(ResearchTask.batch_id == first).order_by(ResearchTask.id)
        )
    )
    assert first == second
    assert batch is not None
    assert batch.period_key == "2026-08"
    assert batch.supplier_count == 2
    assert batch.graph_version == "research-graph-v2"
    assert batch.budget_snapshot["max_queries"] == 3
    assert len(tasks) == 2
    assert {task.task_type for task in tasks} == {"monthly"}
    assert all(task.supplier_scope and len(task.supplier_scope) == 1 for task in tasks)
    assert all(task.execution_requested_at is not None for task in tasks)
    assert {task.supplier_scope[0] for task in tasks} == {
        item["supplier_id"] for item in batch.supplier_snapshot
    }


def _configure_monthly_batch_job(db_session, monkeypatch, owner: User) -> None:
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_ENABLED", True)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_CRON", "0 9 1 * *")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_TOPIC", "月报试点风险")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_ORCHESTRATOR", "langgraph")
    monkeypatch.setattr(
        research_schedule,
        "get_search_settings",
        lambda: SearchSettings("bocha", "configured-for-test", "", 15, 2_000),
    )


def test_monthly_default_scope_limits_150_enabled_suppliers_to_100(
    db_session, monkeypatch
) -> None:
    owner = User(
        username="monthly-limited-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.add_all(
        [
            Supplier(
                supplier_code=f"LIMITED-{index:03d}",
                legal_name=f"试点供应商{index}",
                country_code="CN",
            )
            for index in range(150)
        ]
    )
    db_session.commit()
    _configure_monthly_batch_job(db_session, monkeypatch, owner)
    monkeypatch.setattr(research_schedule, "RESEARCH_MONTHLY_SUPPLIER_SCOPE", "limited")
    monkeypatch.setattr(research_schedule, "RESEARCH_MONTHLY_SUPPLIER_LIMIT", 100)

    batch_id = scheduler_jobs.create_monthly_research_batch_job(
        now=datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    batch = db_session.get(ResearchBatch, batch_id)
    tasks = list(
        db_session.scalars(select(ResearchTask).where(ResearchTask.batch_id == batch_id))
    )
    assert batch is not None
    assert batch.supplier_count == 100
    assert len(tasks) == 100
    assert [item["supplier_code"] for item in batch.supplier_snapshot] == [
        f"LIMITED-{index:03d}" for index in range(100)
    ]


def test_monthly_all_scope_includes_all_150_enabled_suppliers(db_session, monkeypatch) -> None:
    owner = User(
        username="monthly-all-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.add_all(
        [
            Supplier(
                supplier_code=f"ALL-{index:03d}",
                legal_name=f"全量供应商{index}",
                country_code="CN",
            )
            for index in range(150)
        ]
    )
    db_session.commit()
    _configure_monthly_batch_job(db_session, monkeypatch, owner)
    monkeypatch.setattr(research_schedule, "RESEARCH_MONTHLY_SUPPLIER_SCOPE", "all")

    batch_id = scheduler_jobs.create_monthly_research_batch_job(
        now=datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    batch = db_session.get(ResearchBatch, batch_id)
    tasks = list(
        db_session.scalars(select(ResearchTask).where(ResearchTask.batch_id == batch_id))
    )
    assert batch is not None
    assert batch.supplier_count == 150
    assert len(tasks) == 150


def test_monthly_research_batch_skips_when_preflight_quota_is_insufficient(
    db_session, monkeypatch
) -> None:
    owner = User(
        username="monthly-quota-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.add(
        Supplier(
            supplier_code="MONTHLY-QUOTA-001",
            legal_name="月报额度测试供应商",
            country_code="CN",
            enabled=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_ENABLED", True)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_CRON", "0 9 1 * *")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_TOPIC", "全供应商月度风险")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)
    monkeypatch.setattr(
        research_schedule,
        "get_search_settings",
        lambda: SearchSettings("bocha", "configured-for-test", "", 15, 552),
    )

    result = scheduler_jobs.create_monthly_research_batch_job(
        now=datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert result is None
    assert db_session.scalar(select(ResearchBatch)) is None


def test_periodic_batch_is_capacity_blocked_by_previous_period(
    db_session, monkeypatch
) -> None:
    owner = User(
        username="capacity-block-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    supplier = Supplier(
        supplier_code="CAPACITY-001",
        legal_name="容量阻塞测试供应商",
        country_code="CN",
        enabled=True,
    )
    db_session.add_all([owner, supplier])
    db_session.flush()
    db_session.add(
        ResearchBatch(
            owner_user_id=owner.id,
            period_type="monthly",
            period_key="2026-07",
            period_start=datetime(2026, 7, 1, tzinfo=ZoneInfo("Asia/Shanghai")).date(),
            period_end=datetime(2026, 7, 31, tzinfo=ZoneInfo("Asia/Shanghai")).date(),
            status="queued",
        )
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_ENABLED", True)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_CRON", "0 9 1 * *")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_TOPIC", "全供应商月度风险")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)
    monkeypatch.setattr(
        research_schedule,
        "get_search_settings",
        lambda: SearchSettings("bocha", "configured-for-test", "", 15, 2_000),
    )

    batch_id = scheduler_jobs.create_monthly_research_batch_job(
        now=datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    batch = db_session.get(ResearchBatch, batch_id)

    assert batch is not None
    assert batch.status == "capacity_blocked"
    assert batch.supplier_count == 1
    assert batch.queued_count == 0
    assert "前一monthly批次" in (batch.error or "")
    assert db_session.scalar(select(ResearchTask).where(ResearchTask.batch_id == batch.id)) is None


def test_capacity_blocked_batch_recovers_after_previous_period_finishes(
    db_session, monkeypatch
) -> None:
    owner = User(
        username="capacity-recovery-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    supplier = Supplier(
        supplier_code="CAPACITY-RECOVER-001",
        legal_name="容量恢复测试供应商",
        country_code="CN",
        enabled=True,
    )
    db_session.add_all([owner, supplier])
    db_session.flush()
    previous = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-07",
        period_start=datetime(2026, 7, 1).date(),
        period_end=datetime(2026, 7, 31).date(),
        status="queued",
    )
    db_session.add(previous)
    db_session.flush()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_ENABLED", True)
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_CRON", "0 9 1 * *")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_MONTHLY_TOPIC", "全供应商月度风险")
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)
    monkeypatch.setattr(
        research_schedule,
        "get_search_settings",
        lambda: SearchSettings("bocha", "configured-for-test", "", 15, 2_000),
    )
    blocked_id = scheduler_jobs.create_monthly_research_batch_job(
        now=datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    previous.status = "succeeded"
    db_session.commit()

    activated = scheduler_jobs.recover_capacity_blocked_research_batches_job(
        now=datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    batch = db_session.get(ResearchBatch, blocked_id)
    tasks = list(
        db_session.scalars(
            select(ResearchTask).where(ResearchTask.batch_id == blocked_id)
        )
    )

    assert activated == 1
    assert batch is not None and batch.status == "queued"
    assert batch.error is None
    assert len(tasks) == 1
    assert tasks[0].topic == "全供应商月度风险"
    assert tasks[0].execution_requested_at is not None
    assert scheduler_jobs.recover_capacity_blocked_research_batches_job(now=datetime.now()) == 0


def test_pending_signal_processing_skips_overlapping_batch(monkeypatch) -> None:
    entered = Event()
    release = Event()
    session_factory_calls = 0

    class _BlockingSession(_FakeSession):
        def execute(self, query: object) -> list[SimpleNamespace]:
            del query
            entered.set()
            assert release.wait(timeout=2)
            return []

    def session_factory() -> _BlockingSession:
        nonlocal session_factory_calls
        session_factory_calls += 1
        return _BlockingSession([])

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", session_factory)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(scheduler_jobs._process_pending_signals)
        assert entered.wait(timeout=1)
        second = executor.submit(scheduler_jobs._process_pending_signals)

        assert second.result(timeout=1) == 0
        release.set()
        assert first.result(timeout=1) == 0

    assert session_factory_calls == 1


def test_pending_signal_processing_skips_disabled_source(
    db_session, monkeypatch
) -> None:
    source = DataSource(
        code="disabled-source",
        name="停用信源",
        source_type="api",
        credibility=80,
        enabled=False,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add(
        RawSignal(
            source_id=source.id,
            external_id="DISABLED-001",
            title="停用信源历史信号",
            content="该信号不应进入当前分析队列。",
            fingerprint="disabled-signal-fingerprint",
            raw_data={},
        )
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))

    assert scheduler_jobs._process_pending_signals(limit=20) == 0
    assert db_session.scalar(select(AIAnalysisRecord)) is None


# ---------------------------------------------------------------------------
# 天眼查周度分片 job（Todo 8）：注册两个连续日 + 分片参数 + 门禁/锁跳过
# ---------------------------------------------------------------------------


def test_tyc_shard_jobs_registered_for_sunday_and_monday() -> None:
    """周日 shard0、周一 shard1；job id/args/触发日/timezone 可测试（不启动长驻进程）。"""
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    scheduler_main._register_tyc_shard_jobs(scheduler)

    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"tyc-shard-0", "tyc-shard-1"}
    anchor = datetime(2026, 9, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周二
    expected = {"tyc-shard-0": ("2026-10-04", 6), "tyc-shard-1": ("2026-10-05", 0)}
    for job_id, (date_text, weekday) in expected.items():
        job = jobs[job_id]
        assert list(job.args) == [int(job_id.rsplit("-", 1)[1])]
        fire = job.trigger.get_next_fire_time(None, anchor)
        assert fire is not None
        assert fire.strftime("%Y-%m-%d") == date_text
        assert fire.weekday() == weekday  # 6=周日、0=周一
        assert (fire.hour, fire.minute) == (6, 0)


def test_tyc_shard_registration_is_idempotent() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    scheduler_main._register_tyc_shard_jobs(scheduler)
    original = str(scheduler.get_job("tyc-shard-0").trigger)  # type: ignore[union-attr]
    scheduler_main._register_tyc_shard_jobs(scheduler)

    shard_jobs = [job for job in scheduler.get_jobs() if job.id.startswith("tyc-shard-")]
    assert len(shard_jobs) == 2
    assert str(scheduler.get_job("tyc-shard-0").trigger) == original  # type: ignore[union-attr]


def test_collect_tyc_shard_job_passes_shard_index_and_processes(
    db_session, monkeypatch
) -> None:
    from app.agent.tyc_batch import SHARD_COUNT
    from app.agent.tyc_batch_models import TycBatchResult

    source = db_session.scalar(select(DataSource).where(DataSource.code == "tianyancha"))
    assert source is not None
    captured: dict[str, object] = {}
    processed: list[int] = []

    def _fake_run(session, target_source, *, shard_index, shard_count):
        del session
        captured.update(
            source_id=target_source.id, shard_index=shard_index, shard_count=shard_count
        )
        return TycBatchResult(
            source_id=target_source.id,
            shard_index=shard_index,
            shard_count=shard_count,
            supplier_id=None,
            targeted_count=3,
            attempted_count=2,
            created_count=1,
            duplicate_count=0,
            empty_count=1,
            failed_count=0,
            quota_exhausted=False,
            per_tool_counts={},
        )

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "run_tyc_batch", _fake_run)
    monkeypatch.setattr(
        scheduler_jobs,
        "_process_pending_signals",
        lambda *args, **kwargs: processed.append(1),
    )

    scheduler_jobs.collect_tyc_shard_job(1)

    assert captured == {
        "source_id": source.id,
        "shard_index": 1,
        "shard_count": SHARD_COUNT,
    }
    assert processed == [1]


def test_collect_tyc_shard_job_skips_on_bucket_gate_rejection(
    db_session, monkeypatch, caplog
) -> None:
    import logging

    from app.agent.tyc_batch_models import TycBatchBucketGateRejected

    caplog.set_level(logging.WARNING, logger="scheduler")
    processed: list[int] = []

    def _rejected(session, target_source, *, shard_index, shard_count):
        del session, target_source, shard_index, shard_count
        raise TycBatchBucketGateRejected(
            "单桶 39 家 × 13 次 > 日额度 500",
            actual_max_bucket=39,
            calls_per_supplier=13,
            daily_limit=500,
        )

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "run_tyc_batch", _rejected)
    monkeypatch.setattr(
        scheduler_jobs, "_process_pending_signals", lambda *a, **k: processed.append(1)
    )

    scheduler_jobs.collect_tyc_shard_job(0)

    assert processed == []
    assert any("门禁" in record.getMessage() for record in caplog.records)


def test_collect_tyc_shard_job_skips_when_batch_lock_busy(
    db_session, monkeypatch, caplog
) -> None:
    import logging

    from app.agent.tyc_batch_models import TycBatchLocked

    caplog.set_level(logging.WARNING, logger="scheduler")
    processed: list[int] = []

    def _locked(session, target_source, *, shard_index, shard_count):
        del session, target_source, shard_index, shard_count
        raise TycBatchLocked("天眼查批量核查已在运行")

    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "run_tyc_batch", _locked)
    monkeypatch.setattr(
        scheduler_jobs, "_process_pending_signals", lambda *a, **k: processed.append(1)
    )

    scheduler_jobs.collect_tyc_shard_job(1)

    assert processed == []
    assert any("跳过" in record.getMessage() for record in caplog.records)


def test_collect_tyc_shard_job_persists_usage_and_report_signal(
    db_session, committed_tyc_env, monkeypatch
) -> None:
    """分片 job 复用 run_tyc_batch：逐工具真提交记账 + 报告信号 + 处理链。"""
    from tyc_batch_support import (
        MultidimMcpStub,
        committed_tyc_daily_used,
        committed_tyc_rows,
        configure_committed_tyc,
    )

    import app.agent.tyc_gateway as tyc_gateway_module
    from app.agent.tyc_batch import bucket_index

    configure_committed_tyc(
        daily_limit=100,
        monthly_limit=1000,
        dimensions=["get_risk_overview", "get_judicial_case"],
    )
    code = next(
        candidate
        for candidate in (f"SUP-TYC-JOB-{index:03d}" for index in range(100))
        if bucket_index(candidate) == 1
    )
    supplier = Supplier(
        supplier_code=code,
        legal_name="分片 job 报告有限公司",
        country_code="CN",
        enabled=True,
    )
    db_session.add(supplier)
    db_session.flush()
    stub = MultidimMcpStub()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        tyc_gateway_module,
        "build_tyc_gateway",
        lambda **kwargs: stub.gateway(["get_risk_overview", "get_judicial_case"]),
    )
    processed: list[int] = []
    monkeypatch.setattr(
        scheduler_jobs, "_process_pending_signals", lambda *a, **k: processed.append(1)
    )

    scheduler_jobs.collect_tyc_shard_job(1)

    assert committed_tyc_rows() == [
        ("search_companies", "分片 job 报告有限公司", "success"),
        ("get_risk_overview", "分片 job 报告有限公司", "success"),
        ("get_judicial_case", "分片 job 报告有限公司", "success"),
    ]
    assert committed_tyc_daily_used() == 3
    signals = list(
        db_session.scalars(
            select(RawSignal).where(RawSignal.external_id.like(f"tyc-{code}-%"))
        )
    )
    assert len(signals) == 1
    assert signals[0].raw_data["report_kind"] == "supplier_profile"
    assert processed == [1]


# ---------------------------------------------------------------------------
# 排期快照同步（0057）：真实 next_run_time 落库、排除内部 job、移除即删除
# ---------------------------------------------------------------------------


def test_job_registry_snapshot_uses_real_next_run_time_and_removes_departed(
    db_session, monkeypatch
) -> None:
    """Given 调度器注册了业务/维护 job，When 同步快照，Then 只落非内部 job 的真实排期。"""
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def _noop() -> None:
        return None

    scheduler.add_job(_noop, "interval", seconds=60, id="collect", name="定时采集与处理")
    scheduler.add_job(
        _noop, "interval", seconds=60, id="source-7", name="采集信息源 ofac-sdn"
    )
    scheduler.add_job(
        _noop, "interval", seconds=60, id="runtime-heartbeat", name="持久化调度器心跳"
    )
    scheduler.add_job(_noop, "interval", seconds=60, id="notify", name="风险提醒推送")
    collect_job = scheduler.get_job("collect")
    source_job = scheduler.get_job("source-7")
    notify_job = scheduler.get_job("notify")
    assert collect_job is not None
    assert source_job is not None
    assert notify_job is not None
    collect_fire = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
    source_fire = collect_fire + timedelta(minutes=5)
    collect_job.next_run_time = collect_fire
    source_job.next_run_time = source_fire
    # 暂停/无下一次触发的 job：如实写 NULL，不推算。
    notify_job.next_run_time = None
    monkeypatch.setattr(
        scheduler_observability, "SessionLocal", lambda: nullcontext(db_session)
    )

    assert scheduler_observability.sync_job_registry(scheduler) is True

    rows = {
        row.job_id: row for row in db_session.scalars(select(SchedulerJobRegistry))
    }
    assert set(rows) == {"collect", "source-7", "notify"}
    assert rows["collect"].name == "定时采集与处理"
    assert rows["collect"].next_run_at == collect_fire
    assert rows["source-7"].next_run_at == source_fire
    assert rows["notify"].next_run_at is None

    # When 移除一个 job 后再次同步，Then 该行从 registry 删除，其余保留。
    scheduler.remove_job("source-7")
    assert scheduler_observability.sync_job_registry(scheduler) is True
    remaining = {row.job_id for row in db_session.scalars(select(SchedulerJobRegistry))}
    assert remaining == {"collect", "notify"}

    # 就地改名/改排期的 job 在下次同步后更新，不产生新行。
    updated_fire = collect_fire + timedelta(hours=1)
    collect_job.name = "定时采集与处理（新）"
    collect_job.next_run_time = updated_fire
    assert scheduler_observability.sync_job_registry(scheduler) is True
    rows = {
        row.job_id: row for row in db_session.scalars(select(SchedulerJobRegistry))
    }
    assert rows["collect"].name == "定时采集与处理（新）"
    assert rows["collect"].next_run_at == updated_fire


def test_job_registry_sync_failure_is_isolated(monkeypatch) -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    def _broken_session() -> NoReturn:
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler_observability, "SessionLocal", _broken_session)

    # 观测失败只返回 False，不向上抛、不影响调度器。
    assert scheduler_observability.sync_job_registry(scheduler) is False


def test_job_registry_sync_tolerates_pending_job_missing_next_run_time(
    db_session, monkeypatch
) -> None:
    """pending Job 缺 next_run_time 槽位：同步成功、如实落 NULL，之后更新真实值。"""

    class _PendingJob:
        """模拟 APScheduler 3.11.3 启动前 pending Job：next_run_time 槽位未赋值。"""

        __slots__ = ("id", "name")

        def __init__(self, job_id: str, name: str) -> None:
            self.id = job_id
            self.name = name

        @property
        def next_run_time(self) -> datetime:
            raise AttributeError("next_run_time")

    class _ReadyJob:
        """启动完成后同一 job：next_run_time 可读。"""

        def __init__(self, job_id: str, name: str, next_run_at: datetime | None) -> None:
            self.id = job_id
            self.name = name
            self.next_run_time = next_run_at

    class _MutableScheduler:
        def __init__(self, jobs: list[JobLike]) -> None:
            self.jobs = jobs

        def get_jobs(self) -> Sequence[JobLike]:
            return list(self.jobs)

    monkeypatch.setattr(
        scheduler_observability, "SessionLocal", lambda: nullcontext(db_session)
    )
    db_session.execute(delete(SchedulerJobRegistry))
    db_session.flush()

    # Given 启动中 get_jobs 返回尚未处理的 pending Job（属性访问抛 AttributeError），
    # When 同步快照，Then 不失败，且该行如实落 next_run_at=NULL。
    scheduler = _MutableScheduler([_PendingJob("source-9", "采集信息源 pending")])
    assert scheduler_observability.sync_job_registry(scheduler) is True
    pending_row = db_session.scalar(
        select(SchedulerJobRegistry).where(SchedulerJobRegistry.job_id == "source-9")
    )
    assert pending_row is not None
    assert pending_row.name == "采集信息源 pending"
    assert pending_row.next_run_at is None

    # When 同一 job 属性已可读后再次同步，Then 更新为真实 UTC 排期。
    fire = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    scheduler.jobs = [_ReadyJob("source-9", "采集信息源 pending", fire)]
    assert scheduler_observability.sync_job_registry(scheduler) is True
    db_session.expire_all()
    updated_row = db_session.scalar(
        select(SchedulerJobRegistry).where(SchedulerJobRegistry.job_id == "source-9")
    )
    assert updated_row is not None
    assert updated_row.next_run_at == datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
