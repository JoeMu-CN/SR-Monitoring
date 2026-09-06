"""风险信号入库时的一次性有效期策略解析与快照持久化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.signals.lifecycle import apply_lifecycle_action
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput, SourceValidityPolicy
from app.signals.sources import RawSourceItem
from app.signals.supersession import insert_supersession, is_supersession_candidate
from app.signals.validity import (
    LifecycleAction,
    PolicyResolutionRequest,
    SignalValidityFacts,
    SourceValidityPolicyConfig,
    ValidityConfigurationError,
    ValidityPolicy,
    ValidityProfile,
    ValidityState,
    calculate_validity_window,
    resolve_policy,
    source_validity_policy_version,
)

_INSERT_BATCH_SIZE = 1000


@dataclass(frozen=True, slots=True)
class SignalIngestionError(Exception):
    """信号结构合法，但有效期或生命周期元数据无法安全入库。"""

    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class LifecycleAuthority:
    """可信生命周期动作的服务端身份，不接受正文或上传字段覆盖。"""

    role: str
    actor_id: str


@dataclass(frozen=True, slots=True)
class SignalIngestion:
    """一次信号入库所需的已解析输入。"""

    source: DataSource
    signal: ManualSignalInput
    fingerprint: str
    collected_at: datetime
    authority: LifecycleAuthority
    anchor_fallback_source: str | None = None
    raw_data: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SignalValiditySnapshot:
    """可直接持久化的不可变有效期计算结果。"""

    profile: str | None
    state: str
    valid_from: datetime
    valid_until: datetime | None
    review_due_at: datetime | None
    mode: str | None
    policy_version: str | None
    reason: dict[str, object]


def persist_signal_ingestions(
    session: Session, ingestions: list[SignalIngestion]
) -> int:
    """在调用方事务中原子应用生命周期动作并写入信号快照。"""
    created = 0
    rows: list[dict[str, object]] = []
    for ingestion in ingestions:
        snapshot = _resolve_validity_snapshot(ingestion)
        apply_lifecycle_action(session, ingestion, snapshot)
        row = _signal_row(ingestion, snapshot)
        if is_supersession_candidate(ingestion, snapshot):
            created += insert_supersession(session, ingestion, snapshot, row)
        else:
            rows.append(row)
    for start in range(0, len(rows), _INSERT_BATCH_SIZE):
        result = session.execute(
            insert(RawSignal)
            .values(rows[start : start + _INSERT_BATCH_SIZE])
            .on_conflict_do_nothing(
                index_elements=[RawSignal.source_id, RawSignal.fingerprint]
            )
            .returning(RawSignal.id)
        )
        created += len(result.scalars().all())
    return created


def merge_source_metadata(
    signal: ManualSignalInput, item: RawSourceItem
) -> ManualSignalInput:
    """以适配器可信结构化字段补齐标准化信号。"""
    return ManualSignalInput.model_validate(
        {
            **signal.model_dump(mode="python"),
            "valid_until": item.valid_until,
            "event_end_at": item.event_end_at,
            "validity_profile": item.validity_profile,
            "validity_key": item.validity_key,
            "lifecycle_action": item.lifecycle_action,
            "target_signal_id": item.target_signal_id,
            "lifecycle_reason": item.lifecycle_reason,
        }
    )


def _source_policy(
    source: DataSource,
    profile: ValidityProfile | None,
) -> tuple[SourceValidityPolicy | None, ValidityPolicy | None]:
    if source.validity_policy is None:
        return None, None
    try:
        config = SourceValidityPolicy.model_validate(source.validity_policy)
        return config, config.to_policy(profile)
    except (ValidationError, ValidityConfigurationError) as exc:
        raise SignalIngestionError("invalid_source_validity_policy") from exc


def _policy_version(policy: ValidityPolicy) -> str:
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


def _resolve_validity_snapshot(ingestion: SignalIngestion) -> SignalValiditySnapshot:
    signal = ingestion.signal
    source_config, source_policy = _source_policy(
        ingestion.source, signal.validity_profile
    )
    decision = resolve_policy(
        PolicyResolutionRequest(
            signal_policy=None,
            source_policy=source_policy,
            profile=signal.validity_profile if source_config is None else None,
            event_subtype=None,
            allow_ai_classification=True,
            requires_ai_classification=True,
        )
    )
    anchor = (signal.published_at or ingestion.collected_at).astimezone(UTC)
    if decision.state is ValidityState.PENDING_CLASSIFICATION:
        if signal.lifecycle_action is not LifecycleAction.ASSERT:
            raise SignalIngestionError("lifecycle_action_requires_resolved_policy")
        return SignalValiditySnapshot(
            profile=None,
            state=decision.state,
            valid_from=anchor,
            valid_until=None,
            review_due_at=None,
            mode=None,
            policy_version=None,
            reason={
                "code": "pending_classification",
                "anchor_source": (
                    "published_at" if signal.published_at is not None else "collected_at"
                ),
                "details": {},
            },
        )
    policy = decision.policy
    if policy is None:
        raise SignalIngestionError("resolved_policy_missing")
    deadline_precedes_anchor = (
        signal.valid_until is not None
        and signal.valid_until.astimezone(UTC) < anchor
    )
    if deadline_precedes_anchor and signal.lifecycle_action not in {
        LifecycleAction.REVOKE,
        LifecycleAction.SUPERSEDE,
    }:
        raise SignalIngestionError("valid_until_before_valid_from")
    try:
        window = calculate_validity_window(
            policy,
            SignalValidityFacts(
                published_at=signal.published_at,
                collected_at=ingestion.collected_at,
                official_valid_until=signal.valid_until,
                event_end_at=signal.event_end_at,
                severity="high",
                validity_key=signal.validity_key,
                lifecycle_action=signal.lifecycle_action,
            ),
            now_utc=ingestion.collected_at,
        )
    except ValidityConfigurationError as exc:
        raise SignalIngestionError(exc.code) from exc
    version = (
        source_config.fingerprint()
        if source_config is not None
        else _policy_version(policy)
    )
    if signal.published_at is None:
        details: dict[str, object] = {}
        if ingestion.anchor_fallback_source is not None:
            details["source"] = ingestion.anchor_fallback_source
        reason: dict[str, object] = {
            "code": "anchor_fallback",
            "anchor_source": "collected_at",
            "details": details,
        }
    else:
        reason = {
            "code": "policy_resolved",
            "anchor_source": window.anchor_source,
            "details": {
                "policy_source": (
                    decision.source.value if decision.source is not None else "profile"
                )
            },
        }
    return SignalValiditySnapshot(
        profile=window.profile,
        state=window.state,
        valid_from=window.valid_from,
        valid_until=window.valid_from if deadline_precedes_anchor else window.valid_until,
        review_due_at=window.review_due_at,
        mode=window.mode,
        policy_version=version,
        reason=reason,
    )


def _signal_row(
    ingestion: SignalIngestion, snapshot: SignalValiditySnapshot
) -> dict[str, object]:
    signal = ingestion.signal
    return {
        "source_id": ingestion.source.id,
        "external_id": signal.external_id,
        "title": signal.title,
        "content": signal.content,
        "url": str(signal.url) if signal.url else None,
        "published_at": signal.published_at,
        "collected_at": ingestion.collected_at,
        "fingerprint": ingestion.fingerprint,
        "raw_data": (
            ingestion.raw_data
            if ingestion.raw_data is not None
            else signal.model_dump(mode="json")
        ),
        "validity_profile": snapshot.profile,
        "validity_state": snapshot.state,
        "valid_from": snapshot.valid_from,
        "valid_until": snapshot.valid_until,
        "review_due_at": snapshot.review_due_at,
        "validity_mode": snapshot.mode,
        "validity_key": signal.validity_key,
        "lifecycle_action": signal.lifecycle_action,
        "validity_policy_version": snapshot.policy_version,
        "validity_reason": snapshot.reason,
    }
