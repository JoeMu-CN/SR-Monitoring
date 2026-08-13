"""数据源调度注册表动态刷新测试。"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

import app.scheduler.jobs as scheduler_jobs
import app.scheduler.main as scheduler_main
from app.auth.models import User
from app.research.models import ResearchTask


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
