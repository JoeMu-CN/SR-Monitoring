"""风险信号统一有效期领域契约。"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, assert_never

from app.ai.schemas import EventSubtype, Severity


class ValidityProfile(StrEnum):
    WEATHER_ALERT = "weather_alert"
    GEOLOGICAL_HAZARD = "geological_hazard"
    PUBLIC_HEALTH_RESTRICTION = "public_health_restriction"
    INDUSTRIAL_ACCIDENT = "industrial_accident"
    REGIONAL_RESOURCE_CONSTRAINT = "regional_resource_constraint"
    TRANSPORT_DISRUPTION = "transport_disruption"
    PUBLIC_SECURITY = "public_security"
    ARMED_CONFLICT = "armed_conflict"
    POLITICAL_INSTABILITY = "political_instability"
    SANCTIONS = "sanctions"
    EXPORT_CONTROL = "export_control"
    TRADE_TARIFF = "trade_tariff"
    POLICY_DRAFT = "policy_draft"
    REGULATORY_CHANGE = "regulatory_change"
    COMPLIANCE_VIOLATION = "compliance_violation"
    JUDICIAL_CASE = "judicial_case"
    ADVERSE_REGISTRY = "adverse_registry"
    CORPORATE_DISTRESS = "corporate_distress"
    BANKRUPTCY_PROCEEDING = "bankruptcy_proceeding"
    CYBER_INCIDENT = "cyber_incident"
    MARKET_PRICE_POINT = "market_price_point"
    RAW_MATERIAL_SHORTAGE = "raw_material_shortage"
    MONTHLY_MACRO_INDICATOR = "monthly_macro_indicator"
    INDUSTRY_CAPACITY_SHIFT = "industry_capacity_shift"
    REPUTATION_EVENT = "reputation_event"
    SUPPLIER_PERFORMANCE_INCIDENT = "supplier_performance_incident"
    OTHER = "other"


class ValidityMode(StrEnum):
    FIXED_DAYS = "fixed_days"
    UNTIL_SUPERSEDED = "until_superseded"
    UNTIL_REVOKED = "until_revoked"
    EVENT_END_PLUS_GRACE = "event_end_plus_grace"
    INDEFINITE = "indefinite"


class ValidityState(StrEnum):
    PENDING_CLASSIFICATION = "pending_classification"
    ACTIVE = "active"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    CONFLICTED = "conflicted"
    LEGACY = "legacy"


class LifecycleAction(StrEnum):
    ASSERT = "assert"
    CONFIRM = "confirm"
    REVOKE = "revoke"
    SUPERSEDE = "supersede"


class PolicySource(StrEnum):
    SIGNAL = "signal"
    SOURCE = "source"
    PROFILE = "profile"
    EVENT_SUBTYPE = "event_subtype"


class AnchorSource(StrEnum):
    PUBLISHED_AT = "published_at"
    COLLECTED_AT = "collected_at"
    OFFICIAL_VALID_UNTIL = "official_valid_until"
    EVENT_END = "event_end"


@dataclass(frozen=True, slots=True)
class ValidityConfigurationError(Exception):
    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class MissingValidityProfileError(Exception):
    def __str__(self) -> str:
        return "validity profile requires AI classification or an explicit source profile"


def _validate_days(value: int | None, name: str) -> None:
    if value is None:
        raise ValidityConfigurationError(f"{name}_required")
    if not 1 <= value <= 3650:
        raise ValidityConfigurationError(f"{name}_out_of_range")


@dataclass(frozen=True, slots=True)
class ValidityPolicy:
    """一个 profile 在采集时快照的不可变有效期策略。"""

    profile: ValidityProfile
    mode: ValidityMode
    fixed_days: int | None = None
    grace_days: int | None = None
    critical_grace_days: int | None = None
    review_days: int | None = None
    review_required: bool = True

    def __post_init__(self) -> None:
        match self.mode:
            case ValidityMode.FIXED_DAYS | ValidityMode.UNTIL_SUPERSEDED:
                _validate_days(self.fixed_days, "fixed_days")
                if any(
                    value is not None
                    for value in (self.grace_days, self.critical_grace_days, self.review_days)
                ):
                    raise ValidityConfigurationError("fixed_days_mode_has_incompatible_parameters")
            case ValidityMode.UNTIL_REVOKED:
                if self.fixed_days is not None or self.grace_days is not None:
                    raise ValidityConfigurationError("until_revoked_has_expiry_days")
                if self.critical_grace_days is not None:
                    raise ValidityConfigurationError("until_revoked_has_critical_grace")
                if self.review_required:
                    _validate_days(self.review_days, "review_days")
                elif self.review_days is not None:
                    raise ValidityConfigurationError("review_disabled_has_review_days")
            case ValidityMode.EVENT_END_PLUS_GRACE:
                _validate_days(self.grace_days, "grace_days")
                if self.fixed_days is not None or self.review_days is not None:
                    raise ValidityConfigurationError("event_end_mode_has_incompatible_parameters")
                if self.critical_grace_days is not None:
                    _validate_days(self.critical_grace_days, "critical_grace_days")
            case ValidityMode.INDEFINITE:
                if any(
                    value is not None
                    for value in (
                        self.fixed_days,
                        self.grace_days,
                        self.critical_grace_days,
                        self.review_days,
                    )
                ):
                    raise ValidityConfigurationError("indefinite_cannot_have_deadline")
                if self.review_required:
                    raise ValidityConfigurationError("indefinite_cannot_require_review")
            case unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class SourceValidityPolicyConfig:
    """信源策略的可版本化配置；profile 缺失表示由单条信号或分类补齐。"""

    profile: ValidityProfile | None
    mode: ValidityMode
    fixed_days: int | None
    grace_days: int | None
    critical_grace_days: int | None
    review_days: int | None
    review_required: bool


def canonicalize_source_validity_policy(policy: SourceValidityPolicyConfig) -> bytes:
    """将信源策略编码为字段顺序无关的稳定 JSON 字节序列。"""
    return json.dumps(
        {
            "critical_grace_days": policy.critical_grace_days,
            "fixed_days": policy.fixed_days,
            "grace_days": policy.grace_days,
            "mode": policy.mode,
            "profile": policy.profile,
            "review_days": policy.review_days,
            "review_required": policy.review_required,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def source_validity_policy_version(policy: SourceValidityPolicyConfig) -> str:
    """返回策略规范化内容的 SHA-256 版本指纹。"""
    return hashlib.sha256(canonicalize_source_validity_policy(policy)).hexdigest()


@dataclass(frozen=True, slots=True)
class SignalValidityFacts:
    published_at: datetime | None
    collected_at: datetime
    official_valid_until: datetime | None
    event_end_at: datetime | None
    severity: Severity
    validity_key: str | None
    lifecycle_action: LifecycleAction


@dataclass(frozen=True, slots=True)
class ValidityWindow:
    profile: ValidityProfile
    mode: ValidityMode
    state: ValidityState
    valid_from: datetime
    valid_until: datetime | None
    review_due_at: datetime | None
    anchor_source: AnchorSource


@dataclass(frozen=True, slots=True)
class PolicyResolutionRequest:
    signal_policy: ValidityPolicy | None
    source_policy: ValidityPolicy | None
    profile: ValidityProfile | None
    event_subtype: EventSubtype | None
    allow_ai_classification: bool
    requires_ai_classification: bool = False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    state: ValidityState
    policy: ValidityPolicy | None
    source: PolicySource | None


EVENT_SUBTYPE_PROFILES: Final[Mapping[EventSubtype, ValidityProfile]] = MappingProxyType(
    {
        "weather_alert": ValidityProfile.WEATHER_ALERT,
        "geological_hazard": ValidityProfile.GEOLOGICAL_HAZARD,
        "armed_conflict": ValidityProfile.ARMED_CONFLICT,
        "sanctions": ValidityProfile.SANCTIONS,
        "export_control": ValidityProfile.EXPORT_CONTROL,
        "political_instability": ValidityProfile.POLITICAL_INSTABILITY,
        "public_security": ValidityProfile.PUBLIC_SECURITY,
        "trade_tariff": ValidityProfile.TRADE_TARIFF,
        "regulatory_change": ValidityProfile.REGULATORY_CHANGE,
        "raw_material_shortage": ValidityProfile.RAW_MATERIAL_SHORTAGE,
        "transport_disruption": ValidityProfile.TRANSPORT_DISRUPTION,
        "corporate_distress": ValidityProfile.CORPORATE_DISTRESS,
        "judicial_case": ValidityProfile.JUDICIAL_CASE,
        "compliance_violation": ValidityProfile.COMPLIANCE_VIOLATION,
        "other": ValidityProfile.OTHER,
    }
)


DEFAULT_POLICY_MATRIX: Final[Mapping[ValidityProfile, ValidityPolicy]] = MappingProxyType(
    {
        ValidityProfile.WEATHER_ALERT: ValidityPolicy(
            ValidityProfile.WEATHER_ALERT, ValidityMode.FIXED_DAYS, fixed_days=3
        ),
        ValidityProfile.GEOLOGICAL_HAZARD: ValidityPolicy(
            ValidityProfile.GEOLOGICAL_HAZARD,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=7,
            critical_grace_days=30,
        ),
        ValidityProfile.PUBLIC_HEALTH_RESTRICTION: ValidityPolicy(
            ValidityProfile.PUBLIC_HEALTH_RESTRICTION,
            ValidityMode.UNTIL_REVOKED,
            review_days=14,
        ),
        ValidityProfile.INDUSTRIAL_ACCIDENT: ValidityPolicy(
            ValidityProfile.INDUSTRIAL_ACCIDENT,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=14,
            critical_grace_days=30,
        ),
        ValidityProfile.REGIONAL_RESOURCE_CONSTRAINT: ValidityPolicy(
            ValidityProfile.REGIONAL_RESOURCE_CONSTRAINT,
            ValidityMode.UNTIL_REVOKED,
            review_days=7,
        ),
        ValidityProfile.TRANSPORT_DISRUPTION: ValidityPolicy(
            ValidityProfile.TRANSPORT_DISRUPTION,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=7,
            critical_grace_days=14,
        ),
        ValidityProfile.PUBLIC_SECURITY: ValidityPolicy(
            ValidityProfile.PUBLIC_SECURITY,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=7,
        ),
        ValidityProfile.ARMED_CONFLICT: ValidityPolicy(
            ValidityProfile.ARMED_CONFLICT, ValidityMode.UNTIL_REVOKED, review_days=30
        ),
        ValidityProfile.POLITICAL_INSTABILITY: ValidityPolicy(
            ValidityProfile.POLITICAL_INSTABILITY, ValidityMode.FIXED_DAYS, fixed_days=30
        ),
        ValidityProfile.SANCTIONS: ValidityPolicy(
            ValidityProfile.SANCTIONS,
            ValidityMode.UNTIL_REVOKED,
            review_required=False,
        ),
        ValidityProfile.EXPORT_CONTROL: ValidityPolicy(
            ValidityProfile.EXPORT_CONTROL,
            ValidityMode.UNTIL_REVOKED,
            review_required=False,
        ),
        ValidityProfile.TRADE_TARIFF: ValidityPolicy(
            ValidityProfile.TRADE_TARIFF, ValidityMode.UNTIL_REVOKED, review_days=90
        ),
        ValidityProfile.POLICY_DRAFT: ValidityPolicy(
            ValidityProfile.POLICY_DRAFT, ValidityMode.FIXED_DAYS, fixed_days=90
        ),
        ValidityProfile.REGULATORY_CHANGE: ValidityPolicy(
            ValidityProfile.REGULATORY_CHANGE,
            ValidityMode.UNTIL_REVOKED,
            review_days=90,
        ),
        ValidityProfile.COMPLIANCE_VIOLATION: ValidityPolicy(
            ValidityProfile.COMPLIANCE_VIOLATION,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=180,
        ),
        ValidityProfile.JUDICIAL_CASE: ValidityPolicy(
            ValidityProfile.JUDICIAL_CASE,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=180,
        ),
        ValidityProfile.ADVERSE_REGISTRY: ValidityPolicy(
            ValidityProfile.ADVERSE_REGISTRY, ValidityMode.UNTIL_REVOKED, review_days=365
        ),
        ValidityProfile.CORPORATE_DISTRESS: ValidityPolicy(
            ValidityProfile.CORPORATE_DISTRESS, ValidityMode.FIXED_DAYS, fixed_days=90
        ),
        ValidityProfile.BANKRUPTCY_PROCEEDING: ValidityPolicy(
            ValidityProfile.BANKRUPTCY_PROCEEDING,
            ValidityMode.UNTIL_REVOKED,
            review_days=365,
        ),
        ValidityProfile.CYBER_INCIDENT: ValidityPolicy(
            ValidityProfile.CYBER_INCIDENT,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=30,
            critical_grace_days=90,
        ),
        ValidityProfile.MARKET_PRICE_POINT: ValidityPolicy(
            ValidityProfile.MARKET_PRICE_POINT,
            ValidityMode.UNTIL_SUPERSEDED,
            fixed_days=1,
        ),
        ValidityProfile.RAW_MATERIAL_SHORTAGE: ValidityPolicy(
            ValidityProfile.RAW_MATERIAL_SHORTAGE,
            ValidityMode.UNTIL_SUPERSEDED,
            fixed_days=14,
        ),
        ValidityProfile.MONTHLY_MACRO_INDICATOR: ValidityPolicy(
            ValidityProfile.MONTHLY_MACRO_INDICATOR,
            ValidityMode.UNTIL_SUPERSEDED,
            fixed_days=35,
        ),
        ValidityProfile.INDUSTRY_CAPACITY_SHIFT: ValidityPolicy(
            ValidityProfile.INDUSTRY_CAPACITY_SHIFT,
            ValidityMode.UNTIL_SUPERSEDED,
            fixed_days=90,
        ),
        ValidityProfile.REPUTATION_EVENT: ValidityPolicy(
            ValidityProfile.REPUTATION_EVENT, ValidityMode.FIXED_DAYS, fixed_days=14
        ),
        ValidityProfile.SUPPLIER_PERFORMANCE_INCIDENT: ValidityPolicy(
            ValidityProfile.SUPPLIER_PERFORMANCE_INCIDENT,
            ValidityMode.EVENT_END_PLUS_GRACE,
            grace_days=30,
        ),
        ValidityProfile.OTHER: ValidityPolicy(
            ValidityProfile.OTHER, ValidityMode.FIXED_DAYS, fixed_days=30
        ),
    }
)


def resolve_policy(request: PolicyResolutionRequest) -> PolicyDecision:
    """按单条、信源、profile、事件细类四层优先级选择策略。"""
    if request.signal_policy is not None:
        return PolicyDecision(ValidityState.ACTIVE, request.signal_policy, PolicySource.SIGNAL)
    if request.source_policy is not None:
        return PolicyDecision(ValidityState.ACTIVE, request.source_policy, PolicySource.SOURCE)
    if request.profile is not None:
        return PolicyDecision(
            ValidityState.ACTIVE, DEFAULT_POLICY_MATRIX[request.profile], PolicySource.PROFILE
        )
    if request.event_subtype is not None:
        profile = EVENT_SUBTYPE_PROFILES[request.event_subtype]
        return PolicyDecision(
            ValidityState.ACTIVE,
            DEFAULT_POLICY_MATRIX[profile],
            PolicySource.EVENT_SUBTYPE,
        )
    if request.requires_ai_classification:
        if request.allow_ai_classification:
            return PolicyDecision(ValidityState.PENDING_CLASSIFICATION, None, None)
        raise MissingValidityProfileError()
    raise MissingValidityProfileError()


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidityConfigurationError("timezone_required")
    return value.astimezone(UTC)


def _anchor(facts: SignalValidityFacts) -> tuple[datetime, AnchorSource]:
    if facts.published_at is not None:
        return _to_utc(facts.published_at), AnchorSource.PUBLISHED_AT
    return _to_utc(facts.collected_at), AnchorSource.COLLECTED_AT


def _grace_days(policy: ValidityPolicy, severity: Severity) -> int:
    if severity == "critical" and policy.critical_grace_days is not None:
        return policy.critical_grace_days
    if policy.grace_days is None:
        raise ValidityConfigurationError("grace_days_required")
    return policy.grace_days


def _deadlines(
    policy: ValidityPolicy, facts: SignalValidityFacts, anchor: datetime
) -> tuple[datetime | None, datetime | None, AnchorSource]:
    if facts.official_valid_until is not None:
        return _to_utc(facts.official_valid_until), None, AnchorSource.OFFICIAL_VALID_UNTIL
    match policy.mode:
        case ValidityMode.FIXED_DAYS | ValidityMode.UNTIL_SUPERSEDED:
            if policy.fixed_days is None:
                raise ValidityConfigurationError("fixed_days_required")
            return anchor + timedelta(days=policy.fixed_days), None, AnchorSource.PUBLISHED_AT
        case ValidityMode.UNTIL_REVOKED:
            review_due_at = (
                anchor + timedelta(days=policy.review_days) if policy.review_days else None
            )
            return None, review_due_at, AnchorSource.PUBLISHED_AT
        case ValidityMode.EVENT_END_PLUS_GRACE:
            if facts.event_end_at is not None:
                return (
                    _to_utc(facts.event_end_at)
                    + timedelta(days=_grace_days(policy, facts.severity)),
                    None,
                    AnchorSource.EVENT_END,
                )
            return (
                anchor + timedelta(days=_grace_days(policy, facts.severity)),
                None,
                AnchorSource.PUBLISHED_AT,
            )
        case ValidityMode.INDEFINITE:
            return None, None, AnchorSource.PUBLISHED_AT
        case unreachable:
            assert_never(unreachable)


def _state_for_action(action: LifecycleAction) -> ValidityState:
    match action:
        case LifecycleAction.ASSERT | LifecycleAction.CONFIRM:
            return ValidityState.ACTIVE
        case LifecycleAction.REVOKE:
            return ValidityState.REVOKED
        case LifecycleAction.SUPERSEDE:
            return ValidityState.SUPERSEDED
        case unreachable:
            assert_never(unreachable)


def calculate_validity_window(
    policy: ValidityPolicy, facts: SignalValidityFacts, *, now_utc: datetime
) -> ValidityWindow:
    """从固定 UTC 时钟和证据事实计算不可变的信号有效窗口。"""
    now = _to_utc(now_utc)
    if policy.mode is ValidityMode.UNTIL_SUPERSEDED and not facts.validity_key:
        raise ValidityConfigurationError("validity_key_required")
    anchor, anchor_source = _anchor(facts)
    valid_until, review_due_at, deadline_source = _deadlines(policy, facts, anchor)
    state = _state_for_action(facts.lifecycle_action)
    if state is ValidityState.ACTIVE and (
        (valid_until is not None and valid_until <= now)
        or (review_due_at is not None and review_due_at <= now)
    ):
        state = ValidityState.EXPIRED
    return ValidityWindow(
        profile=policy.profile,
        mode=policy.mode,
        state=state,
        valid_from=anchor,
        valid_until=valid_until,
        review_due_at=review_due_at,
        anchor_source=(
            deadline_source
            if deadline_source is not AnchorSource.PUBLISHED_AT
            else anchor_source
        ),
    )


def is_signal_effective(value: ValidityWindow | PolicyDecision, *, now_utc: datetime) -> bool:
    """唯一可复用判活入口；截止等于当前 UTC 时间即失效。"""
    now = _to_utc(now_utc)
    match value:
        case PolicyDecision():
            return False
        case ValidityWindow(state=state, valid_until=valid_until, review_due_at=review_due_at):
            return (
                state is ValidityState.ACTIVE
                and (valid_until is None or valid_until > now)
                and (review_due_at is None or review_due_at > now)
            )
        case unreachable:
            assert_never(unreachable)
