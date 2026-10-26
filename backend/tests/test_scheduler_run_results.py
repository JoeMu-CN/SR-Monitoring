"""调度执行结果摘要白名单与按任务执行历史接口。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import security as auth_security
from app.scheduler.observability_models import SchedulerJobRun
from app.scheduler.run_results import RunResult, RunResultMetric, normalize_run_result

NOW = datetime(2026, 10, 26, 4, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 1. RunResult 校验与规范化
# --------------------------------------------------------------------------- #
def test_normalize_accepts_run_result_instance() -> None:
    value = RunResult(
        summary="本轮完成",
        metrics=[RunResultMetric(label="新增", value=3)],
        outputs=[RunResultMetric(label="报告", value=1)],
    )

    assert normalize_run_result(value) == {
        "summary": "本轮完成",
        "metrics": [{"label": "新增", "value": 3}],
        "outputs": [{"label": "报告", "value": 1}],
    }


def test_normalize_accepts_valid_dict_and_ignores_extra_fields() -> None:
    assert normalize_run_result(
        {
            "summary": "完成",
            "metrics": [{"label": "新增", "value": 0}],
            "outputs": [],
            "secret": "SECRET_RETVAL",
            "traceback": "SECRET_TRACEBACK",
        }
    ) == {
        "summary": "完成",
        "metrics": [{"label": "新增", "value": 0}],
        "outputs": [],
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        1.5,
        True,
        "summary",
        ["summary"],
        RuntimeError("boom"),
        {},
        {"summary": ""},
        {"summary": "x" * 161},
        {"summary": "ok", "metrics": [{"label": "x" * 25, "value": 1}]},
        {"summary": "ok", "metrics": [{"label": "负数", "value": -1}]},
        {"summary": "ok", "metrics": [{"label": "过大", "value": 1_000_001}]},
        {
            "summary": "ok",
            "metrics": [{"label": f"m{index}", "value": 1} for index in range(13)],
        },
        {"summary": "ok", "outputs": [{"label": f"o{index}", "value": 1} for index in range(13)]},
    ],
)
def test_normalize_rejects_untrusted_or_out_of_bounds_values(value: object) -> None:
    # 不可信/越界值一律视为“无摘要”，且绝不抛异常。
    assert normalize_run_result(value) is None


def test_normalize_does_not_mutate_input() -> None:
    raw = {"summary": "完成", "metrics": [{"label": "新增", "value": 2}]}
    original = {
        "summary": "完成",
        "metrics": [{"label": "新增", "value": 2}],
    }

    assert normalize_run_result(raw) is not None
    assert raw == original


# --------------------------------------------------------------------------- #
# 2. 按任务执行历史接口
# --------------------------------------------------------------------------- #
def _clear_runs(session: Session) -> None:
    session.execute(delete(SchedulerJobRun))
    session.flush()


def _run(
    *,
    job_id: str = "collect",
    status: str = "completed",
    scheduled_run_at: datetime = NOW,
    result_summary: dict[str, object] | None = None,
) -> SchedulerJobRun:
    return SchedulerJobRun(
        job_id=job_id,
        name="定时采集与处理",
        status=status,
        scheduled_run_at=scheduled_run_at,
        started_at=scheduled_run_at,
        finished_at=scheduled_run_at,
        result_summary=result_summary,
    )


def test_scheduler_runs_empty_items_for_unknown_job(client, db_session: Session) -> None:
    _clear_runs(db_session)

    response = client.get("/api/v1/system/scheduler-runs/removed-job")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"job_id": "removed-job", "items": []}


def test_scheduler_runs_returns_latest_by_schedule_desc(
    client, db_session: Session
) -> None:
    _clear_runs(db_session)
    db_session.add_all(
        [
            _run(scheduled_run_at=NOW - timedelta(minutes=3)),
            _run(scheduled_run_at=NOW - timedelta(minutes=1)),
            _run(scheduled_run_at=NOW - timedelta(minutes=2)),
            _run(job_id="notify", scheduled_run_at=NOW),
        ]
    )
    db_session.flush()

    response = client.get("/api/v1/system/scheduler-runs/collect", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "collect"
    assert len(body["items"]) == 2
    assert [
        datetime.fromisoformat(item["scheduled_run_at"].replace("Z", "+00:00"))
        for item in body["items"]
    ] == [NOW - timedelta(minutes=1), NOW - timedelta(minutes=2)]


def test_scheduler_runs_projects_result_shape_without_raw_values(
    client, db_session: Session
) -> None:
    _clear_runs(db_session)
    db_session.add(
        _run(
            result_summary={
                "summary": "本轮采集完成",
                "metrics": [{"label": "新增信号", "value": 3}],
                "outputs": [{"label": "处理报告", "value": 1}],
            }
        )
    )
    db_session.add(
        _run(
            scheduled_run_at=NOW - timedelta(minutes=1),
            status="error",
            result_summary=None,
        )
    )
    db_session.flush()

    response = client.get("/api/v1/system/scheduler-runs/collect")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"job_id", "items"}
    first, second = body["items"]
    assert set(first) == {
        "id",
        "job_id",
        "name",
        "status",
        "scheduled_run_at",
        "started_at",
        "finished_at",
        "result",
    }
    assert first["status"] == "completed"
    assert first["result"] == {
        "summary": "本轮采集完成",
        "metrics": [{"label": "新增信号", "value": 3}],
        "outputs": [{"label": "处理报告", "value": 1}],
    }
    assert second["result"] is None
    assert "SECRET" not in response.text
    assert "traceback" not in response.text


def test_scheduler_runs_requires_session(client) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    response = client.get("/api/v1/system/scheduler-runs/collect")

    assert response.status_code == 401


def test_scheduler_runs_requires_source_status_permission(
    client, monkeypatch
) -> None:
    patched = {
        role: (perms - {"source_status_view"})
        for role, perms in auth_security.ROLE_PERMISSIONS.items()
    }
    monkeypatch.setattr(auth_security, "ROLE_PERMISSIONS", patched)

    response = client.get("/api/v1/system/scheduler-runs/collect")

    assert response.status_code == 403


@pytest.mark.parametrize("limit", [0, 21])
def test_scheduler_runs_rejects_out_of_range_limit(client, limit: int) -> None:
    response = client.get(
        "/api/v1/system/scheduler-runs/collect", params={"limit": limit}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("job_id", ["COLLECT", "collect_1", "a" * 121])
def test_scheduler_runs_rejects_invalid_job_id(client, job_id: str) -> None:
    response = client.get(f"/api/v1/system/scheduler-runs/{job_id}")

    assert response.status_code == 422
