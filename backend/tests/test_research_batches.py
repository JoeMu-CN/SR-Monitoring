from datetime import date, datetime

from sqlalchemy import select

from app.research.models import ResearchBatch, ResearchReport
from app.research.reporting import (
    ResearchCitationDraft,
    ResearchClaimDraft,
    ResearchReportDraft,
)
from app.research.service import (
    cancel_batch,
    complete_task,
    create_generated_report,
    create_task,
    refresh_batch_status,
)


def _child_draft(name: str) -> ResearchReportDraft:
    return ResearchReportDraft(
        title=f"{name}子报告",
        disclaimer="AI 生成，仅供参考。",
        facts=[
            ResearchClaimDraft(
                claim_id="risk-1",
                claim_type="fact",
                text=f"{name}存在公开风险信息",
                citation_ids=["source-1"],
                confidence=80,
            )
        ],
        citations=[
            ResearchCitationDraft(
                citation_id="source-1",
                url="https://official.example/risk",
                quote="官方公告摘要",
                verified=True,
            )
        ],
    )


def test_batch_report_aggregates_verified_child_reports_once(db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "batch-report-owner")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        supplier_snapshot=[
            {"supplier_id": 101, "supplier_code": "SUP-101", "legal_name": "供应商一"},
            {"supplier_id": 102, "supplier_code": "SUP-102", "legal_name": "供应商二"},
        ],
        supplier_count=2,
        queued_count=2,
    )
    db_session.add(batch)
    db_session.flush()
    first = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="月报主题",
        supplier_scope=[101],
        idempotency_key="batch-report-101",
        batch_id=batch.id,
    )
    second = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="月报主题",
        supplier_scope=[102],
        idempotency_key="batch-report-102",
        batch_id=batch.id,
    )
    first.status = "succeeded"
    second.status = "failed"
    db_session.commit()
    create_generated_report(
        db_session,
        task_id=first.id,
        draft=_child_draft("供应商一"),
        model_version="fake",
    )

    refreshed = refresh_batch_status(db_session, batch_id=batch.id)
    assert refreshed is not None and refreshed.status == "partial"
    aggregate = db_session.scalar(
        select(ResearchReport).where(ResearchReport.batch_id == batch.id)
    )
    assert aggregate is not None
    assert aggregate.task_id is None
    assert aggregate.model_version == "deterministic-batch-v1"
    assert aggregate.draft_payload["facts"][0]["text"] == "[供应商一] 供应商一存在公开风险信息"
    assert aggregate.draft_payload["facts"][0]["citation_ids"]
    assert all(item["verified"] for item in aggregate.draft_payload["citations"])

    same = refresh_batch_status(db_session, batch_id=batch.id)
    assert same is not None and same.id == refreshed.id
    assert db_session.scalar(
        select(ResearchReport.id).where(ResearchReport.batch_id == batch.id)
    ) == aggregate.id


def test_batch_report_with_no_child_evidence_is_explicit_empty_draft(db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "batch-report-empty")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        supplier_snapshot=[{"supplier_id": 201, "legal_name": "证据不足供应商"}],
        supplier_count=1,
        failed_count=1,
        status="failed",
    )
    db_session.add(batch)
    db_session.flush()
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="证据不足",
        supplier_scope=[201],
        idempotency_key="batch-report-empty-task",
        batch_id=batch.id,
    )
    task.status = "failed"
    db_session.commit()

    refreshed = refresh_batch_status(db_session, batch_id=batch.id)
    aggregate = db_session.scalar(
        select(ResearchReport).where(ResearchReport.batch_id == batch.id)
    )
    assert refreshed is not None and refreshed.status == "failed"
    assert aggregate is not None
    assert aggregate.draft_payload["facts"] == []
    assert aggregate.draft_payload["citations"] == []


def test_batch_with_skipped_supplier_is_partial_and_reportable(db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "batch-report-skipped")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        supplier_snapshot=[{"supplier_id": 202, "legal_name": "已停用供应商"}],
        supplier_count=1,
        queued_count=1,
    )
    db_session.add(batch)
    db_session.flush()
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="停用供应商批次",
        supplier_scope=[202],
        idempotency_key="batch-report-skipped-task",
        batch_id=batch.id,
    )
    task.status = "skipped"
    task.error = "skipped:supplier_disabled"
    db_session.commit()

    refreshed = refresh_batch_status(db_session, batch_id=batch.id)
    aggregate = db_session.scalar(
        select(ResearchReport).where(ResearchReport.batch_id == batch.id)
    )

    assert refreshed is not None
    assert refreshed.status == "partial"
    assert refreshed.skipped_count == 1
    assert refreshed.budget_exhausted_count == 0
    assert aggregate is not None
    assert aggregate.draft_payload["facts"] == []


def test_batch_with_only_budget_exhausted_tasks_has_distinct_status(
    db_session, auth_as
) -> None:
    owner = auth_as("risk_admin", "batch-report-budget")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        supplier_snapshot=[{"supplier_id": 203, "legal_name": "预算耗尽供应商"}],
        supplier_count=1,
        queued_count=1,
    )
    db_session.add(batch)
    db_session.flush()
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="预算耗尽批次",
        supplier_scope=[203],
        idempotency_key="batch-report-budget-task",
        batch_id=batch.id,
    )
    task.status = "budget_exhausted"
    task.error = "budget_exhausted:ResearchBudgetExceeded"
    db_session.commit()

    refreshed = refresh_batch_status(db_session, batch_id=batch.id)

    assert refreshed is not None
    assert refreshed.status == "budget_exhausted"
    assert refreshed.budget_exhausted_count == 1
    assert refreshed.failed_count == 0
    assert db_session.scalar(
        select(ResearchReport).where(ResearchReport.batch_id == batch.id)
    ) is not None


def test_cancel_batch_requests_running_and_cancels_queued_tasks(db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "batch-cancel-owner")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        supplier_snapshot=[
            {"supplier_id": 204, "legal_name": "排队供应商"},
            {"supplier_id": 205, "legal_name": "运行供应商"},
        ],
        supplier_count=2,
        queued_count=2,
    )
    db_session.add(batch)
    db_session.flush()
    queued = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="批次取消",
        supplier_scope=[204],
        idempotency_key="batch-cancel-queued",
        batch_id=batch.id,
    )
    running = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="批次取消",
        supplier_scope=[205],
        idempotency_key="batch-cancel-running",
        batch_id=batch.id,
    )
    running.status = "running"
    running.worker_id = "batch-cancel-worker"
    running.lease_until = datetime.now().astimezone()
    db_session.commit()

    cancelled = cancel_batch(
        db_session,
        batch_id=batch.id,
        owner_user_id=owner.id,
        role="risk_admin",
    )

    assert cancelled is not None
    assert cancelled.status == "running"
    assert cancelled.cancel_requested_at is not None
    assert db_session.get(type(queued), queued.id).status == "cancelled"
    assert db_session.get(type(running), running.id).cancel_requested_at is not None

    completed = complete_task(
        db_session,
        task_id=running.id,
        worker_id="batch-cancel-worker",
        succeeded=True,
    )
    assert completed is not None and completed.status == "cancelled"
    db_session.refresh(cancelled)
    assert cancelled.status == "cancelled"


def test_cancel_batch_api_is_idempotent_for_capacity_blocked_batch(
    client, db_session, auth_as
) -> None:
    owner = auth_as("risk_admin", "batch-cancel-api")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-09",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        supplier_snapshot=[],
        status="capacity_blocked",
        error="等待前序批次",
    )
    db_session.add(batch)
    db_session.commit()

    first = client.post(f"/api/v1/research/batches/{batch.id}/cancel")
    second = client.post(f"/api/v1/research/batches/{batch.id}/cancel")

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"


def test_batch_read_api_exposes_task_and_aggregate_report(client, db_session, auth_as) -> None:
    owner = auth_as("risk_admin", "batch-api-owner")
    batch = ResearchBatch(
        owner_user_id=owner.id,
        period_type="monthly",
        period_key="2026-08",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        supplier_snapshot=[
            {"supplier_id": 301, "supplier_code": "SUP-301", "legal_name": "API供应商"}
        ],
        supplier_count=1,
        queued_count=1,
    )
    db_session.add(batch)
    db_session.flush()
    task = create_task(
        db_session,
        owner_user_id=owner.id,
        task_type="monthly",
        topic="API月报",
        supplier_scope=[301],
        idempotency_key="batch-api-task",
        batch_id=batch.id,
    )
    task.status = "failed"
    db_session.commit()
    refresh_batch_status(db_session, batch_id=batch.id)

    response = client.get(f"/api/v1/research/batches/{batch.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["report_id"] is not None
    assert payload["tasks"][0]["supplier_code"] == "SUP-301"
    assert client.get("/api/v1/research/batches").json()["items"][0]["id"] == batch.id
    reports = client.get(f"/api/v1/research/batches/{batch.id}/reports")
    assert reports.status_code == 200
    assert reports.json()["items"][0]["batch_id"] == batch.id
    assert reports.json()["items"][0]["task_id"] is None
