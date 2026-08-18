from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EventType = Literal[
    "weather",
    "geological",
    "logistics",
    "trade_policy",
    "geopolitical",
    "corporate",
    "judicial",
    "compliance",
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
    "other",
]
Severity = Literal["critical", "high", "medium", "low"]
AffectedActivity = Literal[
    "production", "logistics", "trade", "operations", "judicial", "compliance"
]


class SignalAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: int
    title: NonEmptyText
    content: NonEmptyText
    url: str | None = None
    published_at: datetime | None = None


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
    start_at: datetime | None = None
    end_at: datetime | None = None
    summary_zh: NonEmptyText
    evidence_sentences: list[NonEmptyText] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        for field_name, value in (("start_at", self.start_at), ("end_at", self.end_at)):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field_name} 必须包含时区")
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
