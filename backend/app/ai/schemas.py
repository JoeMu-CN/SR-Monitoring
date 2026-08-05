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


class SignalAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    suggested_severity: Severity
    organizations: list[OrganizationReference] = Field(default_factory=list)
    locations: list[LocationReference] = Field(default_factory=list)
    affected_activities: list[AffectedActivity] = Field(default_factory=list)
    affected_products: list[str] = Field(default_factory=list)
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


class AIAnalysisRecordListResponse(BaseModel):
    items: list[AIAnalysisRecordRead]
    total: int
    limit: int
    offset: int
