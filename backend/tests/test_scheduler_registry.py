"""数据源调度注册表动态刷新测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from threading import Event
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

import app.research.schedule as research_schedule
import app.scheduler.jobs as scheduler_jobs
import app.scheduler.main as scheduler_main
from app.ai.models import AIAnalysisRecord
from app.auth.models import User
from app.config import SearchSettings
from app.research.models import ResearchBatch, ResearchTask
from app.signals.models import DataSource, RawSignal
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
        def scalars(self, query: object) -> list[SimpleNamespace]:
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
