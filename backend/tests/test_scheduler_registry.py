"""数据源调度注册表动态刷新测试。"""

from __future__ import annotations

from types import SimpleNamespace

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
