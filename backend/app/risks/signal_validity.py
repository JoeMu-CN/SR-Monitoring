"""AI 后分类与风险信号有效性适配。"""

from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import SignalAnalysisResult
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import SourceValidityPolicy
from app.signals.validity import (
    EVENT_SUBTYPE_PROFILES,
    AnchorSource,
    LifecycleAction,
    PolicyResolutionRequest,
    SignalValidityFacts,
    SourceValidityPolicyConfig,
    ValidityConfigurationError,
    ValidityMode,
    ValidityProfile,
    ValidityState,
    ValidityWindow,
    calculate_validity_window,
    is_signal_effective,
    resolve_policy,
    source_validity_policy_version,
)


class InactiveRiskSignalError(ValueError):
    """风险处理拒绝非有效信号；异常实例需允许解释器写入 traceback。"""

    def __init__(self, signal_id: int, reason: str) -> None:
        self.signal_id = signal_id
        self.reason = reason
        super().__init__(signal_id, reason)

    def __str__(self) -> str:
        return f"signal {self.signal_id} is not effective: {self.reason}"


def _policy_version(
    policy_config: SourceValidityPolicy | None, profile: ValidityProfile
) -> str:
    if policy_config is not None:
        return policy_config.fingerprint()
    policy = resolve_policy(
        PolicyResolutionRequest(None, None, profile, None, False)
    ).policy
    assert policy is not None
    return source_validity_policy_version(
        SourceValidityPolicyConfig(
            profile=policy.profile,
            mode=policy.mode,
            fixed_days=policy.fixed_days,
            grace_days=policy.grace_days,
            critical_grace_days=policy.critical_grace_days,
            review_days=policy.review_days,
            review_required=policy.review_required,
        )
    )


def mark_classification_failed(
    signal: RawSignal, analysis: AIAnalysisRecord, reason: str
) -> None:
    """保留 pending 供人工复核，并记录稳定 reason code。"""
    signal.validity_state = ValidityState.PENDING_CLASSIFICATION
    signal.validity_reason = {
        "code": "classification_failed",
        "anchor_source": (
            "published_at" if signal.published_at is not None else "collected_at"
        ),
        "details": {"analysis_id": analysis.id, "reason": reason},
    }
    analysis.needs_review = True
    analysis.review_reason = "；".join(
        item for item in (analysis.review_reason, "AI 分类失败，需人工复核") if item
    )


def classify_pending_signal(
    session: Session,
    signal: RawSignal,
    analysis: AIAnalysisRecord,
    result: SignalAnalysisResult,
    *,
    now_utc: datetime,
) -> None:
    """按 AI subtype 补齐策略快照；调用方事务同时决定是否创建事件。"""
    if signal.validity_state != ValidityState.PENDING_CLASSIFICATION:
        return
    if result.event_subtype is None:
        mark_classification_failed(signal, analysis, "event_subtype_missing")
        return
    source = session.get(DataSource, signal.source_id)
    assert source is not None
    try:
        source_config = (
            SourceValidityPolicy.model_validate(source.validity_policy)
            if source.validity_policy is not None
            else None
        )
        profile = EVENT_SUBTYPE_PROFILES[result.event_subtype]
        source_policy = source_config.to_policy(profile) if source_config is not None else None
        decision = resolve_policy(
            PolicyResolutionRequest(
                signal_policy=None,
                source_policy=source_policy,
                profile=profile if source_config is None else None,
                event_subtype=result.event_subtype,
                allow_ai_classification=False,
            )
        )
        policy = decision.policy
        assert policy is not None
        window = calculate_validity_window(
            policy,
            SignalValidityFacts(
                published_at=signal.published_at,
                collected_at=signal.collected_at,
                official_valid_until=None,
                event_end_at=result.end_at,
                severity=result.suggested_severity,
                validity_key=signal.validity_key,
                lifecycle_action=LifecycleAction(signal.lifecycle_action),
            ),
            now_utc=now_utc,
        )
    except (ValidationError, ValidityConfigurationError) as exc:
        mark_classification_failed(signal, analysis, str(exc))
        return
    signal.validity_profile = window.profile
    signal.validity_state = window.state
    signal.valid_from = window.valid_from
    signal.valid_until = window.valid_until
    signal.review_due_at = window.review_due_at
    signal.validity_mode = window.mode
    signal.validity_policy_version = _policy_version(source_config, profile)
    signal.validity_reason = {
        "code": "classification_resolved",
        "anchor_source": window.anchor_source,
        "details": {"event_subtype": result.event_subtype},
    }


def signal_validity_window(signal: RawSignal) -> ValidityWindow | None:
    if (
        signal.validity_state != ValidityState.ACTIVE
        or signal.validity_profile is None
        or signal.validity_mode is None
        or signal.valid_from is None
    ):
        return None
    return ValidityWindow(
        profile=ValidityProfile(signal.validity_profile),
        mode=ValidityMode(signal.validity_mode),
        state=ValidityState(signal.validity_state),
        valid_from=signal.valid_from,
        valid_until=signal.valid_until,
        review_due_at=signal.review_due_at,
        anchor_source=AnchorSource(str(signal.validity_reason["anchor_source"])),
    )


def is_raw_signal_effective(signal: RawSignal, *, now_utc: datetime) -> bool:
    window = signal_validity_window(signal)
    return window is not None and is_signal_effective(window, now_utc=now_utc)


def expire_signal_if_due(signal: RawSignal, *, now_utc: datetime) -> bool:
    if signal.validity_state != ValidityState.ACTIVE or is_raw_signal_effective(
        signal, now_utc=now_utc
    ):
        return False
    signal.validity_state = ValidityState.EXPIRED
    signal.validity_reason = {
        "code": "deadline_reached",
        "anchor_source": signal.validity_reason["anchor_source"],
        "details": {},
    }
    return True
