"""可视化规则工作台的请求/响应模型。"""

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


class DimensionConfigPatch(BaseModel):
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
    forced_rules: list[ForcedRuleUpdate] | None = None

    @field_validator("match_columns", "event_types", "strong_match_types")
    @classmethod
    def unique_values(cls, value: list[object] | None) -> list[object] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("列表中不能包含重复项")
        return value


class DimensionRead(BaseModel):
    """单个监控维度的运行时状态（默认配置 + DB 覆盖合并后）。"""

    key: str
    label: str
    description: str
    content_items: list[str]
    data_sources: list[dict[str, str]]
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
