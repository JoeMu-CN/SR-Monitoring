"""Todo 4 契约：信源级策略移除复核字段，域层复核语义保留。

失败优先说明：实现前 `SourceValidityPolicy` 仍接受 `review_days`/`review_required`，
本文件中的「拒绝 review 键」用例会以 DID NOT RAISE 失败；实现后全部转绿。
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.signals.schemas import SourceValidityPolicy
from app.signals.validity import (
    LifecycleAction,
    PolicyResolutionRequest,
    SignalValidityFacts,
    ValidityMode,
    ValidityProfile,
    calculate_validity_window,
    canonicalize_source_validity_policy,
    resolve_policy,
)

PUBLISHED_AT = datetime(2026, 9, 1, 8, tzinfo=UTC)


def _adverse_facts() -> SignalValidityFacts:
    return SignalValidityFacts(
        published_at=PUBLISHED_AT,
        collected_at=PUBLISHED_AT,
        official_valid_until=None,
        event_end_at=None,
        severity="high",
        validity_key=None,
        lifecycle_action=LifecycleAction.ASSERT,
    )


def test_policy_without_review_keys_is_accepted() -> None:
    policy = SourceValidityPolicy.model_validate({"mode": "fixed_days", "fixed_days": 7})

    assert policy.mode is ValidityMode.FIXED_DAYS
    assert policy.fixed_days == 7
    assert "review_days" not in SourceValidityPolicy.model_fields
    assert "review_required" not in SourceValidityPolicy.model_fields


def test_policy_keeps_extra_forbid_and_frozen() -> None:
    assert SourceValidityPolicy.model_config["extra"] == "forbid"
    assert SourceValidityPolicy.model_config["frozen"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "fixed_days", "fixed_days": 7, "review_required": False},
        {"mode": "until_revoked", "review_days": 30},
        {"mode": "until_revoked", "review_required": True, "review_days": 30},
    ],
)
def test_review_keys_are_rejected_as_extra(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        SourceValidityPolicy.model_validate(payload)

    message = str(error.value)
    assert "review_days" in message or "review_required" in message


def test_until_revoked_source_policy_needs_no_review_days() -> None:
    policy = SourceValidityPolicy(
        profile=ValidityProfile.PUBLIC_HEALTH_RESTRICTION,
        mode=ValidityMode.UNTIL_REVOKED,
    )

    domain_policy = policy.to_policy(profile=None)

    assert domain_policy is not None
    assert domain_policy.mode is ValidityMode.UNTIL_REVOKED
    assert domain_policy.review_days is None
    assert domain_policy.review_required is False


def test_fingerprint_config_uses_domain_compatible_review_defaults() -> None:
    policy = SourceValidityPolicy(
        profile=ValidityProfile.PUBLIC_HEALTH_RESTRICTION,
        mode=ValidityMode.UNTIL_REVOKED,
    )

    config = policy.fingerprint_config()
    canonical = canonicalize_source_validity_policy(config)

    assert config.review_days is None
    assert config.review_required is False
    assert b'"review_days":null' in canonical
    assert b'"review_required":false' in canonical
    assert len(policy.fingerprint()) == 64


def test_adverse_registry_still_emits_review_due_at() -> None:
    decision = resolve_policy(
        PolicyResolutionRequest(
            signal_policy=None,
            source_policy=None,
            profile=ValidityProfile.ADVERSE_REGISTRY,
            event_subtype=None,
            allow_ai_classification=False,
        )
    )

    assert decision.policy is not None
    window = calculate_validity_window(decision.policy, _adverse_facts(), now_utc=PUBLISHED_AT)

    assert window.mode is ValidityMode.UNTIL_REVOKED
    assert window.review_due_at == PUBLISHED_AT + timedelta(days=365)
