from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.research import runner
from app.research.models import ResearchProviderQuotaPeriod, ResearchTaskEvent, ResearchToolRun
from app.research.search import FakeSearchProvider, SearchCandidate
from app.research.service import (
    ResearchProviderQuotaExceeded,
    create_task,
    reconcile_stale_tool_runs,
    reserve_provider_quota,
    settle_provider_quota,
)
from app.research.web import ResearchPageRead


def test_provider_quota_reservation_classifies_and_settles_idempotently(db_session) -> None:
    now = datetime(2026, 8, 17, 1, tzinfo=UTC)
    manual = reserve_provider_quota(
        db_session,
        provider="bocha",
        task_type="manual",
        monthly_limit=2,
        now=now,
    )
    scheduled = reserve_provider_quota(
        db_session,
        provider="bocha",
        task_type="monthly",
        monthly_limit=999,
        now=now,
    )
    assert manual is not None and manual.task_kind == "manual"
    assert scheduled is not None and scheduled.task_kind == "scheduled"

    with pytest.raises(ResearchProviderQuotaExceeded):
        reserve_provider_quota(
            db_session,
            provider="bocha",
            task_type="manual",
            monthly_limit=999,
            now=now,
        )

    reservation = manual.as_dict()
    settle_provider_quota(db_session, reservation=reservation, count_as_used=True)
    db_session.commit()
    settle_provider_quota(db_session, reservation=reservation, count_as_used=True)
    db_session.commit()

    period = db_session.scalar(
        select(ResearchProviderQuotaPeriod).where(
            ResearchProviderQuotaPeriod.provider == "bocha",
            ResearchProviderQuotaPeriod.period_key == "2026-08",
        )
    )
    assert period is not None
    assert period.monthly_limit == 2
    assert period.manual_reserved == 0
    assert period.scheduled_reserved == 1
    assert period.used == 1


def test_fake_provider_does_not_reserve_global_quota(db_session) -> None:
    assert (
        reserve_provider_quota(
            db_session,
            provider="fake",
            task_type="manual",
            monthly_limit=1,
        )
        is None
    )
    assert db_session.scalar(select(ResearchProviderQuotaPeriod)) is None


def test_runner_settles_search_quota_in_tool_ledger(db_session, auth_as, monkeypatch) -> None:
    owner = auth_as("risk_admin", "quota-runner-owner")
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="manual",
        topic="额度搜索集成",
        supplier_scope=[],
        idempotency_key="quota-runner-task",
    )
    task.status = "running"
    task.worker_id = "quota-worker"
    task.lease_until = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()
    provider = FakeSearchProvider(
        provider_name="bocha",
        responses={
            task.topic: (
                SearchCandidate("https://official.example/quota", "额度公告", "摘要"),
            )
        },
    )

    async def fake_reader(url: str) -> ResearchPageRead:
        return ResearchPageRead(
            requested_url=url,
            final_url=url,
            redirect_chain=(),
            status_code=200,
            content_type="text/html",
            excerpt="可回验的额度公告摘要。",
        )

    monkeypatch.setattr(
        runner, "get_search_settings", lambda: SimpleNamespace(monthly_limit=5)
    )
    monkeypatch.setattr(runner, "read_public_page", fake_reader)
    result = runner._execute_topic_source_discovery_round(
        db_session,
        task,
        query=task.topic,
        phase="primary",
        monitoring_count=0,
        provider=provider,
    )
    assert result.saved_count == 1
    quota = db_session.scalar(
        select(ResearchProviderQuotaPeriod).where(
            ResearchProviderQuotaPeriod.provider == "bocha"
        )
    )
    assert quota is not None
    assert quota.used == 1
    assert quota.manual_reserved == 0


def test_stale_tool_run_reconciliation_counts_unknown_provider_request_once(
    db_session, auth_as
) -> None:
    owner = auth_as("risk_admin", "quota-recovery-owner")
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="manual",
        topic="遗留额度对账",
        supplier_scope=[],
        idempotency_key="quota-recovery-task",
    )
    task.status = "running"
    task.worker_id = "crashed-worker"
    task.lease_until = now - timedelta(minutes=10)
    db_session.commit()
    reservation = reserve_provider_quota(
        db_session,
        provider="bocha",
        task_type="manual",
        monthly_limit=5,
        now=now,
    )
    assert reservation is not None
    tool_run = ResearchToolRun(
        task_id=task.id,
        action_id="web_search:stale",
        action_type="web_search",
        arguments_hash="hash",
        usage_snapshot={"provider_quota": reservation.as_dict()},
        started_at=now - timedelta(hours=1),
    )
    db_session.add(tool_run)
    db_session.commit()

    assert (
        reconcile_stale_tool_runs(
            db_session,
            stale_before=now - timedelta(minutes=30),
            now=now,
        )
        == 1
    )
    db_session.refresh(tool_run)
    assert tool_run.status == "failed"
    assert tool_run.error_category == "stale_reconciled"
    assert tool_run.usage_snapshot["provider_quota"]["settled"] is True
    quota = db_session.scalar(
        select(ResearchProviderQuotaPeriod).where(
            ResearchProviderQuotaPeriod.provider == "bocha",
            ResearchProviderQuotaPeriod.period_key == "2026-08",
        )
    )
    assert quota is not None
    assert quota.used == 1
    assert quota.manual_reserved == 0
    assert db_session.scalar(
        select(ResearchTaskEvent).where(
            ResearchTaskEvent.task_id == task.id,
            ResearchTaskEvent.event_type == "tool_run_reconciled",
        )
    ) is not None

    assert (
        reconcile_stale_tool_runs(
            db_session,
            stale_before=now - timedelta(minutes=30),
            now=now,
        )
        == 0
    )
    db_session.refresh(quota)
    assert quota.used == 1


def test_stale_tool_run_reconciliation_keeps_active_lease(db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "quota-recovery-active-owner")
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="manual",
        topic="活动租约对账",
        supplier_scope=[],
        idempotency_key="quota-recovery-active-task",
    )
    task.status = "running"
    task.worker_id = "active-worker"
    task.lease_until = now + timedelta(minutes=10)
    db_session.add(
        ResearchToolRun(
            task_id=task.id,
            action_id="public_page_read:active",
            action_type="public_page_read",
            arguments_hash="hash-active",
            started_at=now - timedelta(hours=1),
        )
    )
    db_session.commit()

    assert (
        reconcile_stale_tool_runs(
            db_session,
            stale_before=now - timedelta(minutes=30),
            now=now,
        )
        == 0
    )
    run = db_session.scalar(
        select(ResearchToolRun).where(ResearchToolRun.task_id == task.id)
    )
    assert run is not None
    assert run.status == "running"
