from datetime import UTC, datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# ISO 3166-1 alpha-2，与 LocationReference.country_code 使用同一约束。
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
# ISO 4217 三字母货币代码，仅用于汇率类事件涉及的具体货币。
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
EventType = Literal[
    "weather",
    "geological",
    "logistics",
    "trade_policy",
    "geopolitical",
    "corporate",
    "judicial",
    "compliance",
    "market",
    "exchange_rate",
    "other",
]
EventSubtype = Literal[
    "weather_alert",
    "geological_hazard",
    "armed_conflict",
    "sanctions",
    "export_control",
    "political_instability",
    "public_security",
    "trade_tariff",
    "regulatory_change",
    "raw_material_shortage",
    "transport_disruption",
    "corporate_distress",
    "judicial_case",
    "compliance_violation",
    "commodity_price_move",
    "industry_market_move",
    "fx_volatility",
    "other",
]
Severity = Literal["critical", "high", "medium", "low"]
SuggestedLevel = Literal["P1", "P2", "P3", "P4"]
AffectedActivity = Literal[
    "production", "logistics", "trade", "operations", "judicial", "compliance"
]
# 行情类事件大类：价格指数、大宗商品与汇率波动，供路由与解析侧统一判定。
MARKET_EVENT_TYPES: Final[frozenset[str]] = frozenset({"market", "exchange_rate"})


class SignalAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: int
    title: NonEmptyText
    content: NonEmptyText
    url: str | None = None
    published_at: datetime | None = None
    # 输入形态：普通信号 / 中央气象台公告类栏目（栏目公告可能一张覆盖多省多时段，
    # 需要公告专用解析规则）。默认 signal，历史调用与测试行为不变。
    content_kind: Literal["signal", "announcement"] = "signal"


class OrganizationReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    aliases: list[str] = Field(default_factory=list)
    registry_no: str | None = None


class LocationReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = None
    city: str | None = None
    district: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=5000)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude 和 longitude 必须同时提供或同时为空")
        if self.radius_km is not None and self.latitude is None:
            raise ValueError("radius_km 只能与坐标同时提供")
        return self


class SignalAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    event_subtype: EventSubtype | None = None
    suggested_severity: Severity
    organizations: list[OrganizationReference] = Field(default_factory=list)
    locations: list[LocationReference] = Field(default_factory=list)
    affected_activities: list[AffectedActivity] = Field(default_factory=list)
    affected_products: list[str] = Field(default_factory=list)
    affected_industries: list[str] = Field(default_factory=list)
    affected_countries: list[CountryCode] = Field(default_factory=list)
    affected_currencies: list[CurrencyCode] = Field(default_factory=list)
    start_at: datetime | None = None
    end_at: datetime | None = None
    summary_zh: NonEmptyText
    evidence_sentences: list[NonEmptyText] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0, le=1)
    suggested_level: SuggestedLevel | None = None
    level_rationale: str | None = None

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        # 模型输出的时间可能不带时区（如 deepseek-v4-flash 返回
        # "2026-07-15T10:08:00"），按 UTC 补时区保证一致性（保守默认）。
        if self.start_at is not None and self.start_at.tzinfo is None:
            self.start_at = self.start_at.replace(tzinfo=UTC)
        if self.end_at is not None and self.end_at.tzinfo is None:
            self.end_at = self.end_at.replace(tzinfo=UTC)
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("end_at 不能早于 start_at")
        allowed_subtypes: dict[str, set[str]] = {
            "weather": {"weather_alert"},
            "geological": {"geological_hazard"},
            "geopolitical": {
                "armed_conflict",
                "sanctions",
                "political_instability",
                "public_security",
            },
            "trade_policy": {
                "sanctions",
                "export_control",
                "trade_tariff",
                "regulatory_change",
            },
            "logistics": {"raw_material_shortage", "transport_disruption"},
            "corporate": {"corporate_distress"},
            "judicial": {"judicial_case"},
            "compliance": {"compliance_violation", "sanctions"},
            "market": {"commodity_price_move", "industry_market_move"},
            "exchange_rate": {"fx_volatility"},
            "other": {"other"},
        }
        if self.event_subtype and self.event_subtype not in allowed_subtypes[self.event_type]:
            raise ValueError("event_subtype 与 event_type 不兼容")
        return self


class AIStatusRead(BaseModel):
    provider: str
    model: str
    configured: bool


class AIAnalysisRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    signal_id: int
    provider: str
    model: str
    prompt_version: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    result: SignalAnalysisResult | None
    error: str | None
    needs_review: bool
    review_reason: str | None


class AIAnalysisRecordListResponse(BaseModel):
    items: list[AIAnalysisRecordRead]
    total: int
    limit: int
    offset: int


class AIReviewSummaryRead(BaseModel):
    needs_review: int
    filtered: int
    analyzed_without_alert: int


class AIReviewItemRead(BaseModel):
    id: int
    signal_id: int
    title: str
    content: str
    url: str | None
    provider: str
    model: str
    status: str
    started_at: datetime
    review_reason: str | None
