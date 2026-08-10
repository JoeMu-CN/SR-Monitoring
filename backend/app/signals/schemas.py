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
    endpoint_url: str | None
    auth_type: str
    login_config: dict[str, object]
    credential_ref: str | None
    api_key_configured: bool
    api_key_hint: str | None
    description: str | None
    adapter_config: dict[str, object]
    adapter_status: str
    adapter_version: int
    adapter_published_at: datetime | None
    access_status: Literal["ready", "throttled", "busy", "cooldown"] = "ready"
    access_cooldown_until: datetime | None = None
    access_last_http_status: int | None = None
    access_last_error_kind: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DataSourceSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    source_type: str
    adapter_status: str
    access_status: Literal["ready", "throttled", "busy", "cooldown"] = "ready"
    access_cooldown_until: datetime | None = None
    enabled: bool
    updated_at: datetime


class DataSourceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: NonEmptyText
    name: NonEmptyText
    source_type: NonEmptyText
    credibility: int = Field(ge=0, le=100)
    schedule: str | None = None
    endpoint_url: HttpUrl | None = None
    auth_type: Literal["none", "api_key", "bearer", "basic", "oauth2", "custom"] = "none"
    login_config: dict[str, object] = Field(default_factory=dict)
    credential_ref: str | None = None
    api_key: str | None = Field(default=None, min_length=1)
    description: str | None = None
    adapter_config: dict[str, object] | None = None
    enabled: bool = False

    @field_validator("schedule", "credential_ref", "description", mode="before")
    @classmethod
    def clean_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class DataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText | None = None
    source_type: NonEmptyText | None = None
    credibility: int | None = Field(default=None, ge=0, le=100)
    schedule: str | None = None
    endpoint_url: HttpUrl | None = None
    auth_type: Literal["none", "api_key", "bearer", "basic", "oauth2", "custom"] | None = None
    login_config: dict[str, object] | None = None
    credential_ref: str | None = None
    api_key: str | None = Field(default=None, min_length=1)
    description: str | None = None
    adapter_config: dict[str, object] | None = None
    enabled: bool | None = None

    @field_validator("schedule", "credential_ref", "description", mode="before")
    @classmethod
    def clean_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class DataSourceAuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int | None
    action: str
    actor_role: str
    actor_id: str | None
    changes: dict[str, object]
    created_at: datetime


class DataSourceAuditLogListResponse(BaseModel):
    items: list[DataSourceAuditLogRead]
    total: int
    limit: int
    offset: int


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


class AdapterPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_code: NonEmptyText
    adapter_config: dict[str, object]
    auth_type: Literal["none", "api_key", "bearer"] = "none"
    credential_ref: str | None = None
    login_config: dict[str, object] = Field(default_factory=dict)


class AdapterPreviewResponse(BaseModel):
    fetched_count: int
    items: list[ManualSignalInput]
