"""研究 Worker 心跳与诊断接口。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.research import runner
from app.research.models import ResearchWorkerHeartbeat
from app.research.runner import run_forever
from app.research.service import (
    stop_worker_heartbeat,
    touch_worker_heartbeat,
    upsert_worker_heartbeat,
)


def test_worker_heartbeat_lifecycle(db_session) -> None:
    started = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    heartbeat = upsert_worker_heartbeat(
        db_session,
        worker_id="worker-heartbeat",
        mode="topic_source_discovery",
        orchestrator="langgraph",
        now=started,
    )
    assert heartbeat.status == "online"
    assert heartbeat.started_at == started
    assert heartbeat.last_seen_at == started

    touched = touch_worker_heartbeat(
        db_session,
        worker_id="worker-heartbeat",
        now=started + timedelta(seconds=15),
    )
    assert touched is not None
    assert touched.status == "online"
    assert touched.last_seen_at == started + timedelta(seconds=15)

    stopped = stop_worker_heartbeat(
        db_session,
        worker_id="worker-heartbeat",
        now=started + timedelta(seconds=30),
    )
    assert stopped is not None
    assert stopped.status == "stopped"
    assert stopped.stopped_at == started + timedelta(seconds=30)


def test_worker_loop_registers_and_stops_heartbeat(db_session) -> None:
    stop_event = Event()

    def run_task_once(_session, _worker_id: str, _lease_seconds: int):
        stop_event.set()
        return None

    run_forever(
        worker_id="worker-loop",
        lease_seconds=60,
        poll_seconds=1,
        stop_event=stop_event,
        run_task_once=run_task_once,
        session_factory=lambda: db_session,
        heartbeat_mode="local_lifecycle_test",
        heartbeat_orchestrator="legacy",
        heartbeat_interval_seconds=5,
    )

    heartbeat = db_session.scalar(
        select(ResearchWorkerHeartbeat).where(
            ResearchWorkerHeartbeat.worker_id == "worker-loop"
        )
    )
    assert heartbeat is not None
    assert heartbeat.status == "stopped"
    assert heartbeat.stopped_at is not None


def test_worker_loop_renews_heartbeat_during_long_task(monkeypatch) -> None:
    stop_event = Event()
    heartbeat_touched = Event()
    calls: list[str] = []

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_upsert(_session, **_kwargs):
        calls.append("upsert")
        return object()

    def fake_touch(_session, **_kwargs):
        calls.append("touch")
        heartbeat_touched.set()
        return object()

    def fake_stop(_session, **_kwargs):
        calls.append("stop")
        return object()

    def run_task_once(_session, _worker_id: str, _lease_seconds: int):
        assert heartbeat_touched.wait(1)
        stop_event.set()
        return object()

    monkeypatch.setattr(runner, "upsert_worker_heartbeat", fake_upsert)
    monkeypatch.setattr(runner, "touch_worker_heartbeat", fake_touch)
    monkeypatch.setattr(runner, "stop_worker_heartbeat", fake_stop)

    run_forever(
        worker_id="worker-long-task",
        lease_seconds=60,
        poll_seconds=1,
        stop_event=stop_event,
        run_task_once=run_task_once,
        session_factory=cast(Callable[[], Session], DummySession),
        heartbeat_mode="topic_source_discovery",
        heartbeat_orchestrator="langgraph",
        heartbeat_interval_seconds=0.01,
    )

    assert calls[0] == "upsert"
    assert "touch" in calls
    assert calls[-1] == "stop"


def test_worker_status_reports_offline_without_heartbeat(client) -> None:
    response = client.get("/api/v1/research/worker/status")

    assert response.status_code == 200
    assert response.json()["status"] == "offline"
    assert response.json()["workers"] == []


def test_worker_status_reports_stale_heartbeat(client, db_session) -> None:
    upsert_worker_heartbeat(
        db_session,
        worker_id="worker-stale",
        mode="topic_source_discovery",
        orchestrator="langgraph",
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    response = client.get("/api/v1/research/worker/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "stale"
    assert payload["workers"][0]["status"] == "stale"
    assert payload["workers"][0]["worker_id"] == "worker-stale"
