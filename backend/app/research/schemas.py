"""研究任务 API Schema。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.research.reporting import ResearchReportDraft

TaskType = Literal["manual", "daily", "weekly"]
TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
ReportStatus = Literal["draft", "submitted", "rejected"]
ReportReviewStatus = Literal["pending", "approved", "rejected"]


class ResearchTaskCreate(BaseModel):
    task_type: TaskType = "manual"
    topic: str = Field(min_length=1, max_length=2000)
    supplier_scope: list[int] = Field(default_factory=list, max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ResearchTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    task_type: TaskType
    topic: str
    supplier_scope: list[int]
    budget_snapshot: dict[str, object]
    search_queries_used: int
    search_results_used: int
    input_tokens_used: int
    output_tokens_used: int
    cost_amount: Decimal
    current_step: str | None
    status: TaskStatus
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
    task_id: int
    title: str
    draft: ResearchReportDraft
    status: ReportStatus
    review_status: ReportReviewStatus
    model_version: str | None
    created_at: datetime
    updated_at: datetime


class ResearchReportList(BaseModel):
    items: list[ResearchReportRead]
