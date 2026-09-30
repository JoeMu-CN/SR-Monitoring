from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.agent.tyc_batch_models import ToolOutcomeCounts
from app.agent.tyc_report import TycRiskReport
from app.signals.validity import (
    LifecycleAction,
    SourceValidityPolicyConfig,
    ValidityConfigurationError,
    ValidityMode,
    ValidityPolicy,
    ValidityProfile,
    ValidityState,
    source_validity_policy_version,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SourceValidityPolicy(BaseModel):
    """信源级结构化有效期策略；profile 可由单条信号或 AI 分类补齐。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: ValidityProfile | None = None
    mode: ValidityMode
    fixed_days: int | None = Field(default=None, ge=1, le=3650)
    grace_days: int | None = Field(default=None, ge=1, le=3650)
    critical_grace_days: int | None = Field(default=None, ge=1, le=3650)
    review_days: int | None = Field(default=None, ge=1, le=3650)
    review_required: bool = True

    @model_validator(mode="after")
    def validate_mode_parameters(self) -> Self:
        try:
            ValidityPolicy(
                profile=self.profile or ValidityProfile.OTHER,
                mode=self.mode,
                fixed_days=self.fixed_days,
                grace_days=self.grace_days,
                critical_grace_days=self.critical_grace_days,
                review_days=self.review_days,
                review_required=self.review_required,
            )
        except ValidityConfigurationError as error:
            raise ValueError(str(error)) from error
        return self

    def fingerprint_config(self) -> SourceValidityPolicyConfig:
        """转换为领域层的稳定版本指纹输入。"""
        return SourceValidityPolicyConfig(
            profile=self.profile,
            mode=self.mode,
            fixed_days=self.fixed_days,
            grace_days=self.grace_days,
            critical_grace_days=self.critical_grace_days,
            review_days=self.review_days,
            review_required=self.review_required,
        )

    def fingerprint(self) -> str:
        """返回规范化策略的 SHA-256 版本指纹。"""
        return source_validity_policy_version(self.fingerprint_config())

    def to_policy(self, profile: ValidityProfile | None) -> ValidityPolicy | None:
        """将信源配置与单条信号 profile 合并为可计算的领域策略。"""
        resolved_profile = self.profile or profile
        if resolved_profile is None:
            return None
        return ValidityPolicy(
            profile=resolved_profile,
            mode=self.mode,
            fixed_days=self.fixed_days,
            grace_days=self.grace_days,
            critical_grace_days=self.critical_grace_days,
            review_days=self.review_days,
            review_required=self.review_required,
        )


class SourceValidityPolicyRead(SourceValidityPolicy):
    """持久化策略及服务端生成的不可伪造版本。"""

    version: str | None = Field(default=None, min_length=64, max_length=64)


class ValidityReasonRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyText
    anchor_source: Literal[
        "published_at",
        "collected_at",
        "official_valid_until",
        "event_end",
        "legacy",
    ]
    details: dict[str, object]


def _normalize_validity_policy(
    signal_validity_days: int | None,
    validity_policy: SourceValidityPolicy | None,
) -> SourceValidityPolicy | None:
    if signal_validity_days is None:
        return validity_policy
    if validity_policy is None:
        return SourceValidityPolicy(
            mode=ValidityMode.FIXED_DAYS,
            fixed_days=signal_validity_days,
        )
    if (
        validity_policy.mode is not ValidityMode.FIXED_DAYS
        or validity_policy.fixed_days != signal_validity_days
    ):
        raise ValueError("signal_validity_days 与 validity_policy 配置冲突")
    return validity_policy


class ManualSignalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str | None = None
    title: NonEmptyText
    content: NonEmptyText
    url: HttpUrl | None = None
    published_at: datetime | None = None
    valid_until: datetime | None = None
    event_end_at: datetime | None = None
    validity_profile: ValidityProfile | None = None
    validity_key: str | None = None
    lifecycle_action: LifecycleAction = LifecycleAction.ASSERT
    target_signal_id: int | None = Field(default=None, gt=0)
    lifecycle_reason: str | None = None

    @field_validator(
        "external_id",
        "url",
        "validity_key",
        "lifecycle_reason",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("published_at", "valid_until", "event_end_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("必须包含时区，例如 2026-08-05T08:00:00+08:00")
        return value

    @model_validator(mode="after")
    def validate_validity_metadata(self) -> Self:
        terminal_action = self.lifecycle_action in {
            LifecycleAction.REVOKE,
            LifecycleAction.SUPERSEDE,
        }
        if (
            self.published_at is not None
            and self.valid_until is not None
            and self.valid_until < self.published_at
            and not terminal_action
        ):
            raise ValueError("valid_until 不得早于 published_at")
        if self.lifecycle_action is LifecycleAction.ASSERT:
            if self.target_signal_id is not None or self.lifecycle_reason is not None:
                raise ValueError("assert 动作不得携带生命周期目标或理由")
            return self
        if self.target_signal_id is None and self.validity_key is None:
            raise ValueError("生命周期动作必须提供 target_signal_id 或 validity_key")
        if self.lifecycle_reason is None:
            raise ValueError("生命周期动作必须提供 lifecycle_reason")
        return self


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
    # 累计信号数（raw_signals 历史存量，用于前端展示与最近采集新增区分）。
    total_signal_count: int = 0
    # 有效期内信号数（按信源 signal_validity_days 过滤；永久有效=全部）。
    valid_signal_count: int = 0
    # 信源级信号有效期（天）：None=永久有效。
    signal_validity_days: int | None = None
    validity_policy: SourceValidityPolicyRead | None = None
    validity_policy_version: str | None = None
    applies_to: Literal["new_signals_only"] = "new_signals_only"


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
    validity_policy_version: str | None = None
    applies_to: Literal["new_signals_only"] = "new_signals_only"


class SourceSignalSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    signal_validity_days: int | None


class SourceSignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None
    title: str
    content: str
    url: str | None
    published_at: datetime | None
    collected_at: datetime
    validity_profile: ValidityProfile | None
    validity_state: ValidityState
    valid_from: datetime | None
    valid_until: datetime | None
    review_due_at: datetime | None
    validity_mode: ValidityMode | None
    validity_key: str | None
    lifecycle_action: LifecycleAction
    validity_policy_version: str | None
    validity_reason: ValidityReasonRead
    # 列表受控摘要：报告信号确定性派生重点摘要，其余回落 content 截断；
    # 由路由显式注入，空值由派生层给稳定占位。
    summary: str = ""


class SourceSignalDetailRead(SourceSignalRead):
    """单条采集记录详情：附加结构化报告与超限截断标记。"""

    report: TycRiskReport | None = None
    report_truncated: bool = False


class SourceSignalListResponse(BaseModel):
    source: SourceSignalSourceRead
    items: list[SourceSignalRead]
    total: int
    limit: int
    offset: int


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
    # 信源级信号有效期（天）：None=永久有效；>=1 整数=信号 N 天后过期。
    signal_validity_days: int | None = Field(default=None, ge=1, le=3650)
    validity_policy: SourceValidityPolicy | None = None

    @model_validator(mode="after")
    def normalize_legacy_validity_days(self) -> Self:
        self.validity_policy = _normalize_validity_policy(
            self.signal_validity_days, self.validity_policy
        )
        return self

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
    # 信源级信号有效期（天）：None=永久；>=1=信号 N 天后过期（仅留库不生效）。
    signal_validity_days: int | None = Field(default=None, ge=1, le=3650)
    validity_policy: SourceValidityPolicy | None = None

    @model_validator(mode="after")
    def normalize_legacy_validity_days(self) -> Self:
        self.validity_policy = _normalize_validity_policy(
            self.signal_validity_days, self.validity_policy
        )
        return self

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


class SignalFilterConfigRead(BaseModel):
    """信号过滤规则（DB 覆盖 + 代码默认合并后的生效值）。"""

    model_config = ConfigDict(extra="forbid")

    high_impact: list[str]
    priority_countries: list[str]
    list_sources: list[str]
    commodity_threshold_pct: float = 5.0
    source: Literal["default", "configured"] = "default"
    updated_at: datetime | None = None


class SignalFilterConfigUpdate(BaseModel):
    """信号过滤规则更新（仅更新提供的键；空列表=清空该项回退默认）。"""

    model_config = ConfigDict(extra="forbid")

    high_impact: list[str] | None = None
    priority_countries: list[str] | None = None
    list_sources: list[str] | None = None
    commodity_threshold_pct: float | None = Field(
        None, ge=0, le=100, description="大宗商品涨跌幅阈值（%），低于则免 LLM"
    )


class RunAllSourcesItem(BaseModel):
    """全量刷新单个信息源的结果。"""

    model_config = ConfigDict(extra="forbid")

    source_id: int
    code: str
    status: Literal["succeeded", "failed", "skipped", "error", "deferred"]
    created_count: int = 0
    reason: str | None = None


class RunAllSourcesResult(BaseModel):
    """全量刷新所有可采集信息源的汇总。"""

    model_config = ConfigDict(extra="forbid")

    total: int
    succeeded: int
    failed: int
    deferred: int = 0
    skipped: int
    items: list[RunAllSourcesItem]


class TycBatchRunRead(BaseModel):
    """天眼查手动单供应商核查（run-tyc-batch）的稳定汇总。

    `shard_index` 为该供应商的真实 SHA-256 桶位（便于与周度分片核对），
    `per_tool_counts` 至少按 tool_name 聚合五态计数。
    """

    model_config = ConfigDict(from_attributes=True)

    source_id: int
    shard_index: int
    shard_count: int
    supplier_id: int | None
    targeted_count: int
    attempted_count: int
    created_count: int
    duplicate_count: int
    empty_count: int
    failed_count: int
    quota_exhausted: bool
    per_tool_counts: dict[str, ToolOutcomeCounts]
