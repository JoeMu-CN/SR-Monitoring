"""研究任务 API Schema。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.research.reporting import ResearchReportDraft

TaskType = Literal["manual", "daily", "weekly", "monthly"]
TaskStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "skipped", "budget_exhausted"
]
TaskEventStatus = Literal["pending", "running", "succeeded", "failed", "skipped", "info"]
BatchStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "capacity_blocked",
    "budget_exhausted",
]
BatchPeriodType = Literal["monthly", "weekly"]
ScheduleType = Literal["weekly", "monthly"]
ReportStatus = Literal["draft", "submitted", "rejected"]
ReportReviewStatus = Literal["pending", "approved", "rejected"]
WorkerRuntimeStatus = Literal["online", "stale", "stopped"]
WorkerOverallStatus = Literal["online", "stale", "offline"]


class ResearchTaskCreate(BaseModel):
    task_type: TaskType = "manual"
    topic: str = Field(min_length=1, max_length=2000)
    supplier_scope: list[int] = Field(default_factory=list, max_length=1000)
    source_urls: list[str] = Field(default_factory=list, max_length=5)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_url in values:
            url = raw_url.strip()
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("研究来源仅允许绝对 HTTPS URL")
            if parsed.username or parsed.password:
                raise ValueError("研究来源 URL 不允许包含用户凭据")
            if url not in normalized:
                normalized.append(url)
        return normalized


class ResearchTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    task_type: TaskType
    topic: str
    supplier_scope: list[int]
    source_urls: list[str]
    budget_snapshot: dict[str, object]
    search_queries_used: int
    search_results_used: int
    input_tokens_used: int
    output_tokens_used: int
    cost_amount: Decimal
    current_step: str | None
    status: TaskStatus
    graph_version: str | None
    checkpoint_thread_id: str | None
    execution_requested_at: datetime | None
    cancel_requested_at: datetime | None
    worker_id: str | None
    lease_until: datetime | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class ResearchTaskList(BaseModel):
    items: list[ResearchTaskRead]


class ResearchWorkerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    worker_id: str
    mode: str
    orchestrator: Literal["legacy", "langgraph"]
    status: WorkerRuntimeStatus
    started_at: datetime
    last_seen_at: datetime
    stopped_at: datetime | None


class ResearchWorkerStatusRead(BaseModel):
    checked_at: datetime
    stale_after_seconds: int
    status: WorkerOverallStatus
    workers: list[ResearchWorkerRead]


class ResearchBatchTaskRead(BaseModel):
    id: int
    supplier_id: int | None
    supplier_code: str | None
    supplier_name: str | None
    status: TaskStatus
    current_step: str | None
    error: str | None
    report_id: int | None


class ResearchBatchRead(BaseModel):
    id: int
    owner_user_id: int
    period_type: BatchPeriodType
    period_key: str
    period_start: date
    period_end: date
    supplier_snapshot: list[dict[str, object]]
    supplier_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    budget_exhausted_count: int
    budget_snapshot: dict[str, object]
    status: BatchStatus
    graph_version: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    error: str | None
    report_id: int | None
    tasks: list[ResearchBatchTaskRead]


class ResearchBatchList(BaseModel):
    items: list[ResearchBatchRead]


class ResearchScheduleUpdate(BaseModel):
    enabled: bool = False
    cron_expression: str = Field(default="30 8 * * mon", min_length=1, max_length=100)
    topic_template: str = Field(default="", max_length=2000)
    budget_template: dict[str, object] = Field(default_factory=dict)
    approved_monthly_quota: int | None = Field(default=None, ge=1, le=100000)
    approval_note: str | None = Field(default=None, max_length=1000)


class ResearchSchedulePreflightRead(BaseModel):
    provider: str
    period_key: str
    enabled_supplier_count: int
    monthly_trigger_count: int
    max_searches_per_supplier: int
    estimated_monthly_searches: int
    reserved_searches: int
    required_monthly_searches: int
    provider_monthly_limit: int
    provider_used: int
    provider_reserved: int
    provider_remaining: int
    approved_monthly_quota: int | None
    can_enable: bool
    block_reason: str | None


class ResearchScheduleRead(BaseModel):
    id: int | None
    schedule_type: ScheduleType
    enabled: bool
    cron_expression: str
    topic_template: str
    budget_template: dict[str, object]
    approved_monthly_quota: int | None
    approved_by_user_id: int | None
    approved_at: datetime | None
    approval_note: str | None
    updated_by_user_id: int | None
    created_at: datetime | None
    updated_at: datetime | None
    preflight: ResearchSchedulePreflightRead


class ResearchTaskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    event_type: str
    node_key: str
    parent_node_key: str | None
    status: TaskEventStatus
    label: str
    detail: dict[str, object]
    occurred_at: datetime


class ResearchTaskEventList(BaseModel):
    items: list[ResearchTaskEventRead]
    next_after_id: int


class ResearchSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    url: str
    title: str | None
    source_type: str
    credibility_tier: str
    http_status: int | None
    content_excerpt: str | None
    retrieved_at: datetime


class ResearchSourceList(BaseModel):
    items: list[ResearchSourceRead]


class ResearchAuditEventRead(BaseModel):
    """研究业务审计的最小只读字段；不返回请求来源或完整请求上下文。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    action: str
    resource_type: str | None
    resource_id: str | None
    success: bool
    occurred_at: datetime
    detail: str | None


class ResearchAuditEventList(BaseModel):
    items: list[ResearchAuditEventRead]
    total: int
    limit: int
    offset: int


class ResearchReportCreate(BaseModel):
    draft: ResearchReportDraft
    model_version: str | None = Field(default=None, min_length=1, max_length=200)


class ResearchReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int | None
    batch_id: int | None
    title: str
    draft: ResearchReportDraft
    status: ReportStatus
    review_status: ReportReviewStatus
    model_version: str | None
    created_at: datetime
    updated_at: datetime


class ResearchReportList(BaseModel):
    items: list[ResearchReportRead]
