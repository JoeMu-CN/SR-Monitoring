"""Scheduler 业务 job 返回 RunResult 摘要的覆盖测试。"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import app.notification.service as notification_service
import app.scheduler.consume as consume_module
import app.scheduler.jobs as scheduler_jobs
import app.scheduler.validity_job as validity_job
from app.scheduler.jobs import BatchResult
from app.scheduler.retention import CleanupResult
from app.signals.models import CollectionRun, DataSource

NOW = datetime(2026, 10, 26, 4, 0, tzinfo=UTC)


def _metrics(result: object) -> dict[str, int]:
    assert result is not None
    return {metric.label: metric.value for metric in result.metrics}  # type: ignore[attr-defined]


def _source(session: Session, *, code: str = "result-source") -> DataSource:
    source = DataSource(
        code=code,
        name=f"信源-{code}",
        source_type="api",
        credibility=80,
        enabled=True,
        schedule=None,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    session.add(source)
    session.flush()
    return source


def test_collect_source_job_returns_latest_collection_counts(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(db_session)
    db_session.add(
        CollectionRun(
            source_id=source.id,
            started_at=NOW,
            finished_at=NOW,
            status="succeeded",
            fetched_count=20,
            created_count=5,
            duplicate_count=15,
        )
    )
    db_session.flush()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        scheduler_jobs,
        "_collect_enabled_sources",
        lambda **kwargs: {source.code: 5},
    )

    result = scheduler_jobs.collect_source_job(source.id)

    assert result is not None
    assert result.summary == "采集完成"
    assert _metrics(result) == {"抓取": 20, "新增": 5, "重复": 15}
    assert [(item.label, item.value) for item in result.outputs] == [("新增信号", 5)]


def test_collect_source_job_returns_none_when_collection_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler_jobs,
        "_collect_enabled_sources",
        lambda **kwargs: {"result-source": -1},
    )

    assert scheduler_jobs.collect_source_job(1) is None


def test_collect_job_returns_scheduled_source_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler_jobs,
        "_collect_enabled_sources",
        lambda **kwargs: {"a": 2, "b": 3, "failed": -1},
    )

    result = scheduler_jobs.collect_job()

    assert result is not None
    assert result.summary == "采集完成"
    assert _metrics(result) == {"采集信源": 3, "新增信号": 5, "失败信源": 1}
    assert [(item.label, item.value) for item in result.outputs] == [("新增信号", 5)]


def test_consume_wrapper_returns_processed_filtered_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_connection = object()
    monkeypatch.setattr(
        consume_module, "engine", SimpleNamespace(connect=lambda: nullcontext(fake_connection))
    )
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_ENABLED", True)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_TIME_BUDGET_SECONDS", 0)
    monkeypatch.setattr(
        consume_module, "_try_acquire_consume_lock", lambda connection: True
    )
    monkeypatch.setattr(
        consume_module, "_release_consume_lock", lambda connection: None
    )
    monkeypatch.setattr(
        consume_module.runtime, "record_job_started", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        consume_module.runtime, "record_job_result", lambda *args, **kwargs: True
    )
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(
        consume_module, "time", SimpleNamespace(monotonic=lambda: next(ticks))
    )
    monkeypatch.setattr(
        consume_module,
        "process_signal_batch",
        lambda batch, **kwargs: BatchResult(processed=4, filtered=2, failed=1),
    )

    result = consume_module.consume_pending_signals_job_with_result()

    assert result.summary == "信号消费完成"
    assert _metrics(result) == {"处理": 4, "过滤": 2, "失败": 1}


def test_validity_job_returns_lifecycle_counts(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validity_job, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        validity_job,
        "coordinate_risk_lifecycle",
        lambda session, *, now_utc: SimpleNamespace(
            expired_signals=12, refreshed_events=3, expired_alerts=12
        ),
    )

    result = validity_job.risk_validity_job(now_utc=NOW)

    assert result is not None
    assert result.summary == "有效期物化完成"
    assert _metrics(result) == {"过期信号": 12, "刷新事件": 3, "过期提醒": 12}


def test_expire_job_returns_expired_alert_count(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "expire_alerts", lambda session: 4)

    result = scheduler_jobs.expire_job()

    assert result is not None
    assert result.summary == "提醒失效完成"
    assert _metrics(result) == {"过期提醒": 4}


def test_cleanup_job_returns_retention_counts(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(
        scheduler_jobs, "reconcile_stale_tool_runs", lambda session, **kwargs: 1
    )
    monkeypatch.setattr(
        scheduler_jobs,
        "cleanup_retention",
        lambda session: CleanupResult(
            expired_alerts=2,
            deleted_events=1,
            deleted_signals=100,
            deleted_analysis=80,
            deleted_runs=20,
            deleted_scheduler_runs=30,
        ),
    )

    result = scheduler_jobs.cleanup_job()

    assert result is not None
    assert result.summary == "保留清理完成"
    assert _metrics(result) == {
        "过期提醒": 2,
        "删除事件": 1,
        "删除信号": 100,
        "删除分析": 80,
        "删除采集运行": 20,
        "删除调度历史": 30,
    }


def test_intelligence_report_job_returns_generated_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler_jobs, "generate_pending_intelligence_reports", lambda: 3
    )

    result = scheduler_jobs.intelligence_report_job()

    assert result is not None
    assert result.summary == "情报报告生成完成"
    assert _metrics(result) == {"报告数": 3}


def test_notify_job_returns_scan_counts(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        notification_service,
        "get_notification_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(
        notification_service, "SessionLocal", lambda: nullcontext(db_session)
    )
    monkeypatch.setattr(
        notification_service,
        "scan_and_notify",
        lambda session, settings: {
            "new_alerts": 2,
            "upgraded": 1,
            "recovered": 0,
            "sent": 3,
            "merged": 1,
            "queued": 0,
            "failed": 1,
            "rate_limited": 0,
            "quiet_suppressed": 2,
            "expired_suppressed": 0,
            "backfill_pending": 4,
            "legacy_unlinked": 5,
            "channels": 2,
            "email_targets": 9,
        },
    )

    result = notification_service.notify_job()

    assert result is not None
    assert result.summary == "提醒扫描完成"
    assert len(result.metrics) == 12
    assert _metrics(result)["新提醒"] == 2
    assert _metrics(result)["已发送"] == 3
    assert _metrics(result)["失败"] == 1
    assert _metrics(result)["回填待处理"] == 4
    # 渠道/接收人数属于环境信息，不进入结果摘要。
    assert "渠道" not in _metrics(result)


def test_age_pending_priorities_wrapper_returns_updated_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler_jobs, "age_pending_signal_priorities", lambda **kwargs: 7
    )

    result = scheduler_jobs.age_pending_signal_priorities_job()

    assert result.summary == "优先级老化完成"
    assert _metrics(result) == {"提升信号": 7}
