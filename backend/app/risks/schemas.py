from datetime import datetime

from pydantic import BaseModel

from app.signals.schemas import ValidityReasonRead


class RiskProcessResult(BaseModel):
    signal_id: int
    event_id: int
    event_created: bool
    signal_linked: bool
    alert_ids: list[int]


class RiskAlertRead(BaseModel):
    id: int
    level: str
    score: int
    score_detail: dict[str, object]
    status: str
    supplier_id: int
    supplier_name: str
    event_id: int
    event_type: str
    event_subtype: str | None
    event_summary: str
    event_start_at: datetime | None
    event_end_at: datetime | None
    confidence: float
    match_type: str
    match_reasons: list[str]
    match_evidence: list[dict[str, object]]
    source_title: str
    source_url: str | None
    published_at: datetime | None
    expires_at: datetime | None
    expiry_kind: str
    validity_state: str
    valid_until: datetime | None
    review_due_at: datetime | None
    validity_policy_version: str | None
    validity_reason: ValidityReasonRead
    updated_at: datetime


class RiskAlertListResponse(BaseModel):
    items: list[RiskAlertRead]
    total: int
    limit: int
    offset: int


class LevelCount(BaseModel):
    level: str
    count: int


class EventTypeCount(BaseModel):
    event_type: str
    count: int


class SourceHealthRead(BaseModel):
    id: int
    code: str
    name: str
    enabled: bool
    last_run_at: datetime | None
    last_run_status: str | None


class SourceDistributionItem(BaseModel):
    """来源分布：同一提醒关联到某来源去重计数，多来源可重复归属。

    各来源计数之和可大于当前提醒总数，不当互斥占比使用。
    """

    source_id: int
    code: str
    name: str
    count: int


class DashboardSummary(BaseModel):
    level_counts: list[LevelCount]
    total_current: int
    today_new: int
    type_distribution: list[EventTypeCount]
    recent_alerts: list[RiskAlertRead]
    sources: list[SourceHealthRead]
    as_of: datetime
    window_start: datetime
    window_days: int
    period_new_count: int
    supplier_total: int
    active_supplier_total: int
    source_distribution: list[SourceDistributionItem]
    retention_window_days: int
    history_may_be_partial: bool


class EventSignalEvidence(BaseModel):
    signal_id: int
    title: str
    content: str
    url: str | None
    published_at: datetime | None


class EventDetailRead(BaseModel):
    id: int
    dedup_key: str
    event_type: str
    event_subtype: str | None
    severity: str
    summary: str
    start_at: datetime | None
    end_at: datetime | None
    confidence: float
    created_at: datetime
    validity_state: str
    valid_until: datetime | None
    review_due_at: datetime | None
    validity_policy_version: str | None
    validity_reason: ValidityReasonRead
    signals: list[EventSignalEvidence]
    entities: list[dict[str, object]]
    locations: list[dict[str, object]]
