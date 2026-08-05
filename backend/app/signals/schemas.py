from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, field_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ManualSignalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str | None = None
    title: NonEmptyText
    content: NonEmptyText
    url: HttpUrl | None = None
    published_at: datetime | None = None

    @field_validator("external_id", "url", mode="before")
    @classmethod
    def clean_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("必须包含时区，例如 2026-08-05T08:00:00+08:00")
        return value


class ManualSignalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]
    signals: list[ManualSignalInput] = Field(min_length=1, max_length=5000)


class DataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    source_type: str
    credibility: int
    schedule: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class CollectionRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched_count: int
    created_count: int
    duplicate_count: int
    error: str | None


class CollectionRunListResponse(BaseModel):
    items: list[CollectionRunRead]
    total: int
    limit: int
    offset: int


class SignalImportSummary(BaseModel):
    run_id: int
    fetched_signals: int
    created_signals: int
    duplicate_signals: int
