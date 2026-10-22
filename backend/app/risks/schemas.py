from datetime import datetime

from pydantic import BaseModel, Field

from app.signals.schemas import ValidityReasonRead


class RiskProcessResult(BaseModel):
    signal_id: int
    event_id: int
    event_created: bool
    signal_linked: bool
    alert_ids: list[int]


class MergedEventRead(BaseModel):
    """合并展示组内的单个事件（提醒仍各自保留，不写回数据库）。"""

    alert_id: int
    event_id: int
    level: str
    score: int
    event_type: str
    event_subtype: str | None = None
    event_summary: str
    event_start_at: datetime | None = None
    event_end_at: datetime | None = None
    source_title: str
    source_url: str | None = None
    published_at: datetime | None = None


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
    # 事件生命周期状态；生命周期未定时为 null，前端不得渲染成 "undefined"。
    validity_state: str | None
    # 代表信号的处理状态；无关联信号时为 null。
    processing_state: str | None = None
    valid_until: datetime | None
    review_due_at: datetime | None
    validity_policy_version: str | None
    validity_reason: ValidityReasonRead
    updated_at: datetime
    # 展示聚合：同供应商 + 同区域 + 时间重叠 + 同事件类型时合并为一条。
    # merged_count=1 表示没有合并；merged_events 只含组内其他事件（不含自身）。
    merged_count: int = 1
    merged_events: list[MergedEventRead] = Field(default_factory=list)


class RiskAlertListResponse(BaseModel):
    items: list[RiskAlertRead]
    total: int
    limit: int
    offset: int


class LevelCount(BaseModel):
    level: str
    count: int


class RiskAlertSummary(BaseModel):
    """当前提醒全量分级计数：与列表分页无关，供概览口径直接展示。

    level_counts 恒含 P1-P4 四档且顺序固定；total_current 为各档之和，
    与 /dashboard/summary 的当前统计共用同一判活条件。
    """

    level_counts: list[LevelCount]
    total_current: int


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
    collected_at: datetime
    validity_state: str | None
    processing_state: str = "pending_classification"


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
    validity_state: str | None
    valid_until: datetime | None
    review_due_at: datetime | None
    validity_policy_version: str | None
    validity_reason: ValidityReasonRead
    signals: list[EventSignalEvidence]
    entities: list[dict[str, object]]
    locations: list[dict[str, object]]
