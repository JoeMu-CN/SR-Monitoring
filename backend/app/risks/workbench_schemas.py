"""可视化规则工作台的请求/响应模型。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class _ForcedRuleNamesValidated(BaseModel):
    """强制规则列表的共用校验：名称非空（字段约束）且不重复（新增安全校验）。"""

    forced_rules: list[ForcedRuleUpdate] | None = None

    @field_validator("forced_rules")
    @classmethod
    def unique_forced_rule_names(
        cls, value: list[ForcedRuleUpdate] | None
    ) -> list[ForcedRuleUpdate] | None:
        if value is not None:
            names = [rule.name for rule in value]
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


class DimensionUpdate(BaseModel):
    """更新维度配置：启停与/或参数覆盖。仅提供的字段生效。"""

    enabled: bool | None = None
    config: DimensionConfigPatch | None = None


class DimensionToggle(BaseModel):
    enabled: bool


class SandboxRequest(BaseModel):
    """沙箱测试：构造样例事件，不落库地评估各维度命中与评分。"""

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
