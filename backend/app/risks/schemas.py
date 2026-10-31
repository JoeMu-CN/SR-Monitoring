from datetime import date, datetime

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
    # 增量字段：供有效期展示与失效原因解释；旧数据缺失时保持 None/False/1。
    # valid_from 优先事件 start_at，回退来源发布时间。
    valid_from: datetime | None = None
    # 该成员对应提醒的截止时间（当前有效提醒的 expires_at）。
    expires_at: datetime | None = None
    validity_state: str | None = None
    # 该成员提醒的实时状态：current / expired。
    status: str | None = None
    # 同一 event×supplier 已被更新版本 current 提醒取代时为 True。
    version_superseded: bool = False
    supplier_count: int = 1


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
    # 组级增量口径：同一合并组内的事件数、信源数与有效期范围。
    # 未分组时按自身一行计算，保证旧响应结构与默认值兼容。
    group_status: str = "active"
    group_event_count: int = 1
    group_source_count: int = 0
    group_expires_min: datetime | None = None
    group_expires_max: datetime | None = None


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
    与 /dashboard/summary 的当前统计共用同一判活条件。增量字段（事件、供应商、
    事件簇）取同一 as_of 的当前有效口径，供事件视图统计条直接使用。
    """

    level_counts: list[LevelCount]
    total_current: int
    # 当前有效提醒去重后的事件数（事件级，非提醒级）。
    total_events: int = 0
    # 当前有效提醒去重后的供应商数。
    total_suppliers: int = 0
    # 当前事件簇数；与 /risk-event-clusters/summary 的 current_clusters 同源。
    total_clusters: int = 0


class RiskEventClusterMemberRead(BaseModel):
    """事件簇内的单个成员事件；同一事件只出现一次（按 event_id 去重）。"""

    event_id: int
    summary: str
    source_code: str
    source_title: str
    published_at: datetime | None = None
    # 事件开始时间优先，缺失时回退来源发布时间。
    valid_from: datetime | None = None
    # 该事件当前有效提醒的最早截止时间；没有当前有效提醒时回退全部提醒。
    expires_at: datetime | None = None
    validity_state: str | None = None
    supplier_count: int = 0
    max_level: str
    # 失效原因；仍有效（没有失效提醒行）时为 null。
    expired_reason: str | None = None


class RiskEventClusterRead(BaseModel):
    """同事件类型 + 地区相交 + 时间窗重叠的事件聚合（读侧，不落库）。"""

    cluster_id: str
    event_type: str
    title: str
    region_label: str
    event_count: int
    supplier_count: int
    max_level: str
    level_counts: list[LevelCount]
    expires_min: datetime | None = None
    expires_max: datetime | None = None
    # active / partially_expired / expired
    status: str
    members: list[RiskEventClusterMemberRead] = Field(default_factory=list)


class RiskEventClusterListResponse(BaseModel):
    items: list[RiskEventClusterRead]
    total: int
    limit: int
    offset: int


class RiskEventClusterSummary(BaseModel):
    """当前有效口径的事件、簇、风险组、提醒与供应商计数（同一 as_of）。"""

    current_events: int
    current_clusters: int
    current_groups: int
    current_alerts: int
    current_suppliers: int
    as_of: datetime


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


class TrendPoint(BaseModel):
    """按 UTC 自然日聚合的当前有效提醒组数（组首次入库日归集，与 total_current 同口径）。"""

    date: date
    count: int


class GeoRiskPoint(BaseModel):
    """供应商省/地区点位：有供应商但无提醒时 alert_count=0，前端以空心圆展示。"""

    name: str
    country_code: str | None
    region: str | None
    city: str | None
    latitude: float
    longitude: float
    supplier_count: int
    site_count: int
    alert_count: int
    level_counts: list[LevelCount]
    max_level: str | None


class GeoRiskSitePoint(BaseModel):
    """供应商站点风险点位：一个站点一个点，坐标必须来自站点实际经纬度。"""

    supplier_id: int
    site_id: int
    supplier_name: str
    site_name: str
    address: str
    country_code: str
    region: str | None
    city: str | None
    latitude: float
    longitude: float
    alert_count: int
    level_counts: list[LevelCount]
    max_level: str | None


class GeoSupplierPoint(BaseModel):
    """供应商站点城市聚合点位；仅统计启用供应商且有经纬度的站点。"""

    city: str
    country_code: str
    latitude: float
    longitude: float
    supplier_count: int
    site_count: int


class DashboardAnalytics(BaseModel):
    """大屏地理与趋势数据；与 /dashboard/summary 分开请求，失败不影响主面板。"""

    as_of: datetime
    window_days: int
    trend: list[TrendPoint]
    risk_points: list[GeoRiskPoint]
    risk_sites: list[GeoRiskSitePoint]
    supplier_points: list[GeoSupplierPoint]
    unlocated_alert_count: int


class EventSignalEvidence(BaseModel):
    signal_id: int
    title: str
    content: str
    url: str | None
    published_at: datetime | None
    collected_at: datetime
    validity_state: str | None
    processing_state: str = "pending_classification"


class EventSupplierAlertRead(BaseModel):
    """事件详情内的受影响供应商：一行一个供应商的最新提醒。"""

    supplier_id: int
    supplier_name: str
    level: str
    score: int
    # 提醒状态原始码（current / expired）。
    alert_status: str
    expires_at: datetime | None
    # 该供应商的提醒是否已被同事件的新版本提醒取代。
    version_superseded: bool
    # 对应提醒 ID，供前端跳转提醒详情；契约未强制，缺失时只做展示。
    alert_id: int | None = None


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
    # 受影响供应商；旧客户端缺失该字段时按空态降级。
    suppliers: list[EventSupplierAlertRead] = Field(default_factory=list)
