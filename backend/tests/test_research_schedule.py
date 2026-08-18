"""周期研究运行时开关、额度预检与周报批次测试。"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.research.schedule as research_schedule
import app.scheduler.jobs as scheduler_jobs
from app.auth.models import User
from app.config import SearchSettings
from app.research.models import (
    ResearchBatch,
    ResearchProviderQuotaPeriod,
    ResearchTask,
)
from app.research.schedule import (
    ResearchSchedulePreflightBlocked,
    get_schedule_config,
    save_weekly_schedule_config,
)
from app.suppliers.models import Supplier

NOW = datetime(2026, 8, 12, 8, tzinfo=ZoneInfo("Asia/Shanghai"))


def _search_settings(*, monthly_limit: int = 2_000) -> SearchSettings:
    return SearchSettings(
        provider="bocha",
        api_key="configured-for-test",
        base_url="",
        timeout_seconds=15,
        monthly_limit=monthly_limit,
    )


def _add_supplier(db_session, code: str = "SCHEDULE-001") -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name="周期测试供应商",
        country_code="CN",
        enabled=True,
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


def test_weekly_schedule_defaults_to_closed(client) -> None:
    response = client.get("/api/v1/research/schedules/weekly")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["id"] is None
    assert body["preflight"]["can_enable"] is False


def test_risk_analyst_cannot_modify_weekly_schedule(client, auth_as) -> None:
    auth_as("risk_analyst", "schedule-risk-analyst")

    response = client.put(
        "/api/v1/research/schedules/weekly",
        json={
            "enabled": False,
            "cron_expression": "0 8 * * mon",
            "topic_template": "供应商周报 {period}",
        },
    )

    assert response.status_code == 403


def test_weekly_schedule_rejects_when_provider_is_unconfigured(db_session, monkeypatch) -> None:
    _add_supplier(db_session)
    owner = User(
        username="schedule-provider-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.flush()
    monkeypatch.setattr(
        research_schedule,
        "get_search_settings",
        lambda: SearchSettings("none", "", "", 15, 2_000),
    )

    with pytest.raises(ResearchSchedulePreflightBlocked) as error:
        save_weekly_schedule_config(
            db_session,
            updated_by_user_id=owner.id,
            enabled=True,
            cron_expression="0 8 * * mon",
            topic_template="供应商周报 {period}",
            budget_template={"max_queries": 1},
            approved_monthly_quota=1_005,
            approval_note="领导批准周报预算",
            now=NOW,
        )

    assert error.value.preflight.can_enable is False
    assert "Provider 未配置" in (error.value.preflight.block_reason or "")


def test_weekly_schedule_rejects_when_provider_quota_is_insufficient(
    db_session, monkeypatch
) -> None:
    _add_supplier(db_session)
    owner = User(
        username="schedule-quota-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.add(
        ResearchProviderQuotaPeriod(
            provider="bocha",
            period_key="2026-08",
            monthly_limit=1_005,
            used=1,
            scheduled_reserved=0,
            manual_reserved=0,
        )
    )
    db_session.flush()
    monkeypatch.setattr(research_schedule, "get_search_settings", lambda: _search_settings())

    with pytest.raises(ResearchSchedulePreflightBlocked) as error:
        save_weekly_schedule_config(
            db_session,
            updated_by_user_id=owner.id,
            enabled=True,
            cron_expression="0 8 * * mon",
            topic_template="供应商周报 {period}",
            budget_template={"max_queries": 1},
            approved_monthly_quota=1_005,
            approval_note="领导批准周报预算",
            now=NOW,
        )

    assert "Provider 月度可用额度不足" in (error.value.preflight.block_reason or "")
    assert error.value.preflight.provider_remaining == 1_004


def test_weekly_schedule_preflight_pass_saves_approval_snapshot(db_session, monkeypatch) -> None:
    _add_supplier(db_session)
    owner = User(
        username="schedule-approved-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    db_session.flush()
    monkeypatch.setattr(research_schedule, "get_search_settings", lambda: _search_settings())

    config, preflight = save_weekly_schedule_config(
        db_session,
        updated_by_user_id=owner.id,
        enabled=True,
        cron_expression="0 8 * * mon",
        topic_template="供应商周报 {period}",
        budget_template={"max_queries": 1},
        approved_monthly_quota=1_005,
        approval_note="领导批准周报预算",
        now=NOW,
    )

    assert preflight.can_enable is True
    assert preflight.required_monthly_searches == 1_005
    assert config.enabled is True
    assert config.approved_by_user_id == owner.id
    assert config.approved_at is not None
    assert config.approval_note == "领导批准周报预算"
    assert get_schedule_config(db_session, schedule_type="weekly") is config


def test_weekly_job_reuses_batch_flow_and_fans_out_enabled_suppliers(
    db_session, monkeypatch
) -> None:
    owner = User(
        username="scheduled-weekly-owner",
        password_hash="not-used",
        role="risk_admin",
        status="active",
    )
    db_session.add(owner)
    _add_supplier(db_session, "WEEKLY-001")
    _add_supplier(db_session, "WEEKLY-002")
    db_session.add(
        Supplier(
            supplier_code="WEEKLY-DISABLED",
            legal_name="停用供应商",
            country_code="CN",
            enabled=False,
        )
    )
    db_session.flush()
    monkeypatch.setattr(research_schedule, "get_search_settings", lambda: _search_settings())
    save_weekly_schedule_config(
        db_session,
        updated_by_user_id=owner.id,
        enabled=True,
        cron_expression="0 8 * * mon",
        topic_template="供应商周报 {period}",
        budget_template={"max_queries": 1},
        approved_monthly_quota=1_010,
        approval_note="领导批准周报预算",
        now=NOW,
    )
    db_session.commit()
    monkeypatch.setattr(scheduler_jobs, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(scheduler_jobs, "RESEARCH_SCHEDULE_OWNER_USERNAME", owner.username)

    first = scheduler_jobs.create_weekly_research_batch_job(now=NOW)
    second = scheduler_jobs.create_weekly_research_batch_job(now=NOW)

    assert first == second
    batch = db_session.scalar(select(ResearchBatch).where(ResearchBatch.id == first))
    tasks = list(
        db_session.scalars(
            select(ResearchTask).where(ResearchTask.batch_id == first).order_by(ResearchTask.id)
        )
    )
    assert batch is not None
    assert batch.period_type == "weekly"
    assert batch.period_key == "2026-W33"
    assert batch.supplier_count == 2
    assert len(tasks) == 2
    assert {task.task_type for task in tasks} == {"weekly"}
    assert {task.topic for task in tasks} == {"供应商周报 2026-08"}
    assert all(task.execution_requested_at is not None for task in tasks)
