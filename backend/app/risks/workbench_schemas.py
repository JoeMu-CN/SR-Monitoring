"""可视化规则工作台的请求/响应模型。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.ai.schemas import (
    EventSubtype,
    EventType,
    LocationReference,
    OrganizationReference,
    Severity,
)

MatchColumn = Literal["entity", "location", "product", "country", "industry"]
MatchType = Literal[
    "registry_no",
    "legal_name",
    "alias",
    "site_distance",
    "site_text",
    "product",
    "country",
    "industry",
]
Level = Literal["P1", "P2", "P3", "P4"]
Score = Annotated[int, Field(ge=0, le=100)]


class ForcedRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    event_types: list[EventType] = Field(default_factory=list)
    event_subtypes: list[EventSubtype] = Field(default_factory=list)
    match_types: list[MatchType] = Field(default_factory=list)
    forced_level: Level
    reason: str = Field(min_length=1)

    @field_validator("name", "reason")
    @classmethod
    def reject_blank(cls, value: str, info: ValidationInfo) -> str:
        """拒绝纯空白值；不改写原值（仅用 strip 判空），保证逐字存储与回读。"""
        if not value.strip():
            label = "名称" if info.field_name == "name" else "原因"
            raise ValueError(f"强制规则{label}不能为空白")
        return value


class _ForcedRuleNamesValidated(BaseModel):
    """强制规则列表的共用校验：名称/原因非空白（字段约束）且名称不重复。"""

    forced_rules: list[ForcedRuleUpdate] | None = None

    @field_validator("forced_rules")
    @classmethod
    def unique_forced_rule_names(
        cls, value: list[ForcedRuleUpdate] | None
    ) -> list[ForcedRuleUpdate] | None:
        if value is not None:
            # 去重按 strip 后的规范形式判定；不修改存储值，保证逐字回存。
            names = [rule.name.strip() for rule in value]
            if len(names) != len(set(names)):
                raise ValueError("强制规则名称不能重复")
        return value


class DimensionConfigPatch(_ForcedRuleNamesValidated):
    """允许持久化的规则覆盖；拒绝未知键和越界值。"""

    model_config = ConfigDict(extra="forbid")

    match_columns: list[MatchColumn] | None = None
    event_types: list[EventType] | None = None
    severity_scores: dict[Severity, Annotated[int, Field(ge=0, le=35)]] | None = None
    association_scores: dict[MatchType, Annotated[int, Field(ge=0, le=30)]] | None = None
    credibility_weight: float | None = Field(default=None, ge=0, le=1)
    timeliness_with_date: int | None = Field(default=None, ge=0, le=10)
    timeliness_without_date: int | None = Field(default=None, ge=0, le=10)
    product_relevance_score: int | None = Field(default=None, ge=0, le=5)
    p1_min: Score | None = None
    p2_min: Score | None = None
    p3_min: Score | None = None
    strong_match_types: list[MatchType] | None = None
    alert_expiry_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("match_columns", "event_types", "strong_match_types")
    @classmethod
    def unique_values(cls, value: list[object] | None) -> list[object] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("列表中不能包含重复项")
        return value


class GlobalScoringPatch(_ForcedRuleNamesValidated):
    """全局评分与强制规则覆盖（PUT 为合并语义，只更新传入字段）。

    confirm_disable_forced_rules 是仅查询参数，不在本模型内；extra=forbid
    确保它不能混进请求体，也绝不会被写入配置行。
    """

    model_config = ConfigDict(extra="forbid")

    severity_scores: dict[Severity, Annotated[int, Field(ge=0, le=35)]] | None = None
    association_scores: dict[MatchType, Annotated[int, Field(ge=0, le=30)]] | None = None
    credibility_weight: float | None = Field(default=None, ge=0, le=1)
    timeliness_with_date: int | None = Field(default=None, ge=0, le=10)
    timeliness_without_date: int | None = Field(default=None, ge=0, le=10)
    product_relevance_score: int | None = Field(default=None, ge=0, le=5)
    p1_min: Score | None = None
    p2_min: Score | None = None
    p3_min: Score | None = None
    strong_match_types: list[MatchType] | None = None
    alert_expiry_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("strong_match_types")
    @classmethod
    def unique_strong_match_types(
        cls, value: list[MatchType] | None
    ) -> list[MatchType] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("列表中不能包含重复项")
        return value


class GlobalScoringConfigRead(BaseModel):
    """全局评分与强制规则配置读模型（只描述全局层，不含维度增量）。

    - effective：全局层当前生效值（代码默认 + 全局行）；defaults：全局层默认值，
      其中 forced_rules 默认 = 代码默认（含 RISK_SCORING_CONFIG）∪ 可全局化的
      维度追加；两者均不含维度增量，维度遮蔽通过 shadowed_by 表达。
    - source=configured 表示存在启用中的全局行（enabled 与之一致）。
    - shadowed_by：口径为**全部声明维度**（含未启用者，如 policy）的
      scoring_overrides 中的 severity/association 子键 → 会覆盖它的维度 key 列表。
    - forced_rules_shadowed_by：**已声明维度**（default_dimensions()，含当前停用者）
      中持久化行 config 写了 forced_rules 的维度 key（第五层会整体替换全局强制
      规则，重新启用后即生效）；rule_dimension_configs 中未声明的同表行（如
      signal-filter）不计入。
    - dropped_dimension_rules：因维度未声明事件类型而被排除出全局默认的维度
      强制规则（含 dimension 字段），供 UI 提示保存后不再生效。
    """

    source: Literal["configured", "default"]
    enabled: bool
    effective: dict[str, object]
    defaults: dict[str, object]
    shadowed_by: dict[str, list[str]]
    forced_rules_shadowed_by: list[str]
    dropped_dimension_rules: list[dict[str, object]]


class DimensionSourceRead(BaseModel):
    """维度引用信源的声明意图 + 实时真实状态（data_sources 表 join）。

    declared_status 沿用 DimensionDataSource.status 取值
    （connected / planned / external_tool），语义为"覆盖意图"；
    linked 与 enabled/adapter_status/last_collected_at/valid_signal_count
    来自 data_sources 表的实时状态，未命中的 code 全部为 None 且 linked=false。
    """

    code: str
    name: str
    declared_status: str
    linked: bool
    enabled: bool | None
    adapter_status: str | None
    last_collected_at: datetime | None
    valid_signal_count: int | None


class DimensionInputSourceRead(BaseModel):
    """observed 中单个信源的输入健康度聚合。"""

    code: str
    name: str
    signal_count: int
    latest_at: datetime | None


class DimensionInputsRead(BaseModel):
    """维度输入健康度反查结果。

    declared_* 来自维度声明信源与 data_sources 表的实时 join；
    observed 来自该维度实际接管的提醒所依赖的原始信号按 source_id 聚合。
    """

    declared_total: int
    declared_linked: int
    declared_enabled: int
    observed: list[DimensionInputSourceRead]
    has_input: bool


class DimensionRead(BaseModel):
    """单个监控维度的运行时状态（默认配置 + DB 覆盖合并后）。"""

    key: str
    label: str
    description: str
    content_items: list[str]
    data_sources: list[DimensionSourceRead]
    event_types: list[str]
    match_columns: list[str]
    enabled: bool
    has_override: bool  # 是否存在 DB 覆盖行（区分"默认值"与"用户已调整"）
    active_alerts: int  # 该维度当前生成的有效提醒数
    scoring: dict[str, object]  # 评分参数摘要（severity/关联权重/阈值/强制规则/有效期）


class TraceEventRead(BaseModel):
    """轨迹事件摘要。

    source_name/published_at 来自支持信号中的确定性代表信号
    （coalesce(published_at, collected_at) 最新者，并列取最大 signal_id）；
    事件没有支持信号时两者均为 None，不伪造来源。
    """

    event_type: str
    event_subtype: str | None
    severity: str
    summary: str
    confidence: float
    published_at: datetime | None
    source_name: str | None


class TraceRoutingRead(BaseModel):
    """事件路由：该提醒评分时的历史归属维度。

    key 恒等于提醒自身的 ``score_detail["dimension"]``（不做当前配置的
    resolve_dimension，配置漂移时二者会不一致）；label 是该历史归属维度的
    身份/代码标签（base.label，不可被 DB 覆盖）。提醒行没有逐提醒配置快照，
    因此仅 match_columns 取自该维度**当前**的合并配置，是本结构中唯一的
    "当前"数据。
    """

    key: str
    label: str
    match_columns: list[str]


class TraceMatchRead(BaseModel):
    """供应商匹配柱命中：来自 supplier_event_matches 行，不做二次解释。"""

    match_type: str
    match_reasons: list[str]
    match_evidence: list[dict[str, object]]


class TraceScoreRead(BaseModel):
    """评分构成与最终等级。

    total/level 以提醒行为准（强制规则命中会改写为满分/强制等级），detail 是
    原始的 score_detail 全量；level_cap 与 forced_rule 是从中提取的命中信息，
    未命中时为 None。
    """

    total: int
    level: str
    detail: dict[str, object]
    level_cap: str | None
    forced_rule: dict[str, object] | None


class TraceSampleRead(BaseModel):
    """样例选择器条目：本维度最近一条 current 提醒的摘要。"""

    id: int
    supplier_id: int
    supplier_name: str
    level: str
    event_summary: str
    updated_at: datetime


class DimensionTraceRead(BaseModel):
    """维度运行轨迹。

    available=false 表示该维度当前没有任何 current 提醒（event/routing/match/
    score 均为 None 且 samples 为空，HTTP 仍为 200）。
    """

    available: bool
    event: TraceEventRead | None
    routing: TraceRoutingRead | None
    match: TraceMatchRead | None
    score: TraceScoreRead | None
    samples: list[TraceSampleRead]


class DimensionUpdate(BaseModel):
    """更新维度配置：启停与/或参数覆盖。仅提供的字段生效。"""

    enabled: bool | None = None
    config: DimensionConfigPatch | None = None


class DimensionToggle(BaseModel):
    enabled: bool


class SandboxRequest(BaseModel):
    """沙箱测试：构造样例事件，不落库地评估各维度命中与评分。

    可选草稿字段用于在保存前预览未持久化配置的效果：
    - dimension_key 指定草稿作用的维度（不存在返回 404）；
    - draft_config 是该维度的草稿覆盖（与保存路径相同的合并与边界校验）；
    - global_config 是全局评分/强制规则的草稿覆盖（与全局 PUT 同构的合并语义）。
    预览只评估、不写任何业务表或配置行；不传草稿字段时行为与旧请求完全一致。
    """

    event_type: EventType
    event_subtype: EventSubtype | None = None
    severity: Severity = "medium"
    organizations: list[OrganizationReference] = Field(default_factory=list)
    locations: list[LocationReference] = Field(default_factory=list)
    affected_products: list[str] = Field(default_factory=list)
    affected_industries: list[str] = Field(default_factory=list)
    summary: str = "沙箱测试事件"
    credibility: int = Field(default=80, ge=0, le=100)
    has_published_at: bool = True
    dimension_key: str | None = None
    draft_config: DimensionConfigPatch | None = None
    global_config: GlobalScoringPatch | None = None
