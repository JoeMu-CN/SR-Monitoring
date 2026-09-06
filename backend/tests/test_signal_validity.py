"""统一风险信号有效期领域契约。"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    SignalIngestionError,
    persist_signal_ingestions,
)
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.supersession import advisory_key1, advisory_key2
from app.signals.validity import (
    DEFAULT_POLICY_MATRIX,
    EVENT_SUBTYPE_PROFILES,
    LifecycleAction,
    MissingValidityProfileError,
    PolicyResolutionRequest,
    PolicySource,
    SignalValidityFacts,
    ValidityConfigurationError,
    ValidityMode,
    ValidityPolicy,
    ValidityProfile,
    ValidityState,
    calculate_validity_window,
    is_signal_effective,
    resolve_policy,
)

NOW_UTC = datetime(2026, 9, 5, 12, tzinfo=UTC)
COLLECTED_AT = datetime(2026, 9, 1, 9, tzinfo=UTC)


def facts(**overrides: datetime | str | LifecycleAction | None) -> SignalValidityFacts:
    values: dict[str, datetime | str | LifecycleAction | None] = {
        "published_at": datetime(2026, 9, 1, 8, tzinfo=UTC),
        "collected_at": COLLECTED_AT,
        "official_valid_until": None,
        "event_end_at": None,
        "severity": "high",
        "validity_key": None,
        "lifecycle_action": LifecycleAction.ASSERT,
    }
    values.update(overrides)
    return SignalValidityFacts(**values)


@pytest.mark.parametrize(
    ("profile", "mode"),
    [
        (ValidityProfile.WEATHER_ALERT, ValidityMode.FIXED_DAYS),
        (ValidityProfile.MARKET_PRICE_POINT, ValidityMode.UNTIL_SUPERSEDED),
        (ValidityProfile.SANCTIONS, ValidityMode.UNTIL_REVOKED),
        (ValidityProfile.GEOLOGICAL_HAZARD, ValidityMode.EVENT_END_PLUS_GRACE),
        (ValidityProfile.OTHER, ValidityMode.INDEFINITE),
    ],
)
def test_calculate_validity_window_when_each_mode_is_used(
    profile: ValidityProfile, mode: ValidityMode
) -> None:
    # Given
    policy = (
        ValidityPolicy(profile=profile, mode=mode, review_required=False)
        if mode is ValidityMode.INDEFINITE
        else DEFAULT_POLICY_MATRIX[profile]
    )
    signal_facts = facts(
        published_at=NOW_UTC - timedelta(hours=1),
        collected_at=NOW_UTC - timedelta(hours=1),
        validity_key="usd-cny" if mode is ValidityMode.UNTIL_SUPERSEDED else None,
    )

    # When
    window = calculate_validity_window(policy, signal_facts, now_utc=NOW_UTC)

    # Then
    assert window.state is ValidityState.ACTIVE
    assert window.mode is mode


@pytest.mark.parametrize(
    ("resolution_request", "expected_source", "expected_profile"),
    [
        (
            PolicyResolutionRequest(
                signal_policy=ValidityPolicy(
                    profile=ValidityProfile.OTHER,
                    mode=ValidityMode.FIXED_DAYS,
                    fixed_days=2,
                ),
                source_policy=DEFAULT_POLICY_MATRIX[ValidityProfile.SANCTIONS],
                profile=ValidityProfile.WEATHER_ALERT,
                event_subtype="armed_conflict",
                allow_ai_classification=False,
            ),
            PolicySource.SIGNAL,
            ValidityProfile.OTHER,
        ),
        (
            PolicyResolutionRequest(
                signal_policy=None,
                source_policy=DEFAULT_POLICY_MATRIX[ValidityProfile.SANCTIONS],
                profile=ValidityProfile.WEATHER_ALERT,
                event_subtype="armed_conflict",
                allow_ai_classification=False,
            ),
            PolicySource.SOURCE,
            ValidityProfile.SANCTIONS,
        ),
        (
            PolicyResolutionRequest(
                signal_policy=None,
                source_policy=None,
                profile=ValidityProfile.WEATHER_ALERT,
                event_subtype="armed_conflict",
                allow_ai_classification=False,
            ),
            PolicySource.PROFILE,
            ValidityProfile.WEATHER_ALERT,
        ),
        (
            PolicyResolutionRequest(
                signal_policy=None,
                source_policy=None,
                profile=None,
                event_subtype="armed_conflict",
                allow_ai_classification=False,
            ),
            PolicySource.EVENT_SUBTYPE,
            ValidityProfile.ARMED_CONFLICT,
        ),
    ],
)
def test_resolve_policy_when_priority_sources_compete(
    resolution_request: PolicyResolutionRequest,
    expected_source: PolicySource,
    expected_profile: ValidityProfile,
) -> None:
    # Given / When
    decision = resolve_policy(resolution_request)

    # Then
    assert decision.state is ValidityState.ACTIVE
    assert decision.source is expected_source
    assert decision.policy is not None
    assert decision.policy.profile is expected_profile


@pytest.mark.parametrize(
    ("profile", "severity", "expected_days"),
    [
        (ValidityProfile.GEOLOGICAL_HAZARD, "high", 7),
        (ValidityProfile.GEOLOGICAL_HAZARD, "critical", 30),
        (ValidityProfile.TRANSPORT_DISRUPTION, "high", 7),
        (ValidityProfile.TRANSPORT_DISRUPTION, "critical", 14),
        (ValidityProfile.CYBER_INCIDENT, "high", 30),
        (ValidityProfile.CYBER_INCIDENT, "critical", 90),
    ],
)
def test_calculate_validity_window_when_severity_changes_grace(
    profile: ValidityProfile, severity: str, expected_days: int
) -> None:
    # Given
    policy = DEFAULT_POLICY_MATRIX[profile]
    event_end_at = datetime(2026, 9, 2, tzinfo=UTC)

    # When
    window = calculate_validity_window(
        policy,
        facts(event_end_at=event_end_at, severity=severity),
        now_utc=NOW_UTC,
    )

    # Then
    assert window.valid_until == event_end_at + timedelta(days=expected_days)


def test_calculate_validity_window_when_published_at_is_missing_uses_collected_at() -> None:
    # Given
    policy = DEFAULT_POLICY_MATRIX[ValidityProfile.WEATHER_ALERT]

    # When
    window = calculate_validity_window(policy, facts(published_at=None), now_utc=NOW_UTC)

    # Then
    assert window.valid_from == COLLECTED_AT
    assert window.valid_until == COLLECTED_AT + timedelta(days=3)


def test_calculate_validity_window_when_official_deadline_is_present_takes_precedence() -> None:
    # Given
    official_valid_until = datetime(2026, 9, 7, 12, tzinfo=timezone(timedelta(hours=8)))

    # When
    window = calculate_validity_window(
        DEFAULT_POLICY_MATRIX[ValidityProfile.WEATHER_ALERT],
        facts(official_valid_until=official_valid_until),
        now_utc=NOW_UTC,
    )

    # Then
    assert window.valid_until == datetime(2026, 9, 7, 4, tzinfo=UTC)


def test_is_signal_effective_when_valid_until_equals_now_returns_false() -> None:
    # Given
    window = calculate_validity_window(
        DEFAULT_POLICY_MATRIX[ValidityProfile.WEATHER_ALERT],
        facts(official_valid_until=NOW_UTC),
        now_utc=NOW_UTC,
    )

    # When
    effective = is_signal_effective(window, now_utc=NOW_UTC)

    # Then
    assert effective is False


@pytest.mark.parametrize("fixed_days", [1, 3650])
def test_validity_policy_when_fixed_days_is_within_bounds_accepts_value(fixed_days: int) -> None:
    # Given / When
    policy = ValidityPolicy(
        profile=ValidityProfile.OTHER,
        mode=ValidityMode.FIXED_DAYS,
        fixed_days=fixed_days,
    )

    # Then
    assert policy.fixed_days == fixed_days


@pytest.mark.parametrize("fixed_days", [0, -1, 3651])
def test_validity_policy_when_fixed_days_is_outside_bounds_raises_typed_error(
    fixed_days: int,
) -> None:
    # Given / When / Then
    with pytest.raises(ValidityConfigurationError) as error:
        ValidityPolicy(
            profile=ValidityProfile.OTHER,
            mode=ValidityMode.FIXED_DAYS,
            fixed_days=fixed_days,
        )

    assert error.value.code == "fixed_days_out_of_range"


def test_calculate_validity_window_when_until_superseded_has_no_key_raises_typed_error() -> None:
    # Given
    policy = DEFAULT_POLICY_MATRIX[ValidityProfile.MARKET_PRICE_POINT]

    # When / Then
    with pytest.raises(ValidityConfigurationError) as error:
        calculate_validity_window(policy, facts(), now_utc=NOW_UTC)

    assert error.value.code == "validity_key_required"


def test_validity_policy_when_mode_has_incompatible_parameters_raises_typed_error() -> None:
    # Given / When / Then
    with pytest.raises(ValidityConfigurationError) as error:
        ValidityPolicy(
            profile=ValidityProfile.OTHER,
            mode=ValidityMode.INDEFINITE,
            fixed_days=30,
        )

    assert error.value.code == "indefinite_cannot_have_deadline"


@pytest.mark.parametrize(
    ("subtype", "profile"),
    [
        ("weather_alert", ValidityProfile.WEATHER_ALERT),
        ("geological_hazard", ValidityProfile.GEOLOGICAL_HAZARD),
        ("armed_conflict", ValidityProfile.ARMED_CONFLICT),
        ("sanctions", ValidityProfile.SANCTIONS),
        ("export_control", ValidityProfile.EXPORT_CONTROL),
        ("political_instability", ValidityProfile.POLITICAL_INSTABILITY),
        ("public_security", ValidityProfile.PUBLIC_SECURITY),
        ("trade_tariff", ValidityProfile.TRADE_TARIFF),
        ("regulatory_change", ValidityProfile.REGULATORY_CHANGE),
        ("raw_material_shortage", ValidityProfile.RAW_MATERIAL_SHORTAGE),
        ("transport_disruption", ValidityProfile.TRANSPORT_DISRUPTION),
        ("corporate_distress", ValidityProfile.CORPORATE_DISTRESS),
        ("judicial_case", ValidityProfile.JUDICIAL_CASE),
        ("compliance_violation", ValidityProfile.COMPLIANCE_VIOLATION),
        ("other", ValidityProfile.OTHER),
    ],
)
def test_event_subtype_profiles_when_existing_subtype_is_used_maps_explicitly(
    subtype: str, profile: ValidityProfile
) -> None:
    # Given / When / Then
    assert EVENT_SUBTYPE_PROFILES[subtype] is profile


def test_resolve_policy_when_profile_requires_ai_classification_returns_pending() -> None:
    # Given
    request = PolicyResolutionRequest(
        signal_policy=None,
        source_policy=None,
        profile=None,
        event_subtype=None,
        allow_ai_classification=True,
        requires_ai_classification=True,
    )

    # When
    decision = resolve_policy(request)

    # Then
    assert decision.state is ValidityState.PENDING_CLASSIFICATION
    assert decision.policy is None
    assert is_signal_effective(decision, now_utc=NOW_UTC) is False


def test_resolve_policy_when_ai_classification_is_not_allowed_raises_typed_error() -> None:
    # Given
    request = PolicyResolutionRequest(None, None, None, None, False, True)

    # When / Then
    with pytest.raises(MissingValidityProfileError):
        resolve_policy(request)


def test_resolve_policy_when_no_policy_or_classification_route_exists_raises_typed_error() -> None:
    # Given
    request = PolicyResolutionRequest(
        signal_policy=None,
        source_policy=None,
        profile=None,
        event_subtype=None,
        allow_ai_classification=False,
        requires_ai_classification=False,
    )

    # When / Then
    with pytest.raises(MissingValidityProfileError):
        resolve_policy(request)


def test_default_policy_matrix_when_loaded_contains_each_profile_once() -> None:
    # Given / When
    matrix_profiles = set(DEFAULT_POLICY_MATRIX)

    # Then
    assert matrix_profiles == set(ValidityProfile)
    assert len(DEFAULT_POLICY_MATRIX) == 27
    assert DEFAULT_POLICY_MATRIX[ValidityProfile.OTHER].fixed_days == 30


def _supersession_source(db_session: Session) -> DataSource:
    source = DataSource(
        code="test-supersession",
        name="替代测试信源",
        source_type="api",
        credibility=80,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    db_session.add(source)
    db_session.flush()
    return source


def _supersession_ingestion(
    source: DataSource,
    *,
    external_id: str,
    published_at: datetime,
    fingerprint: str,
    collected_at: datetime,
    validity_key: str = "test-key",
) -> SignalIngestion:
    signal = ManualSignalInput(
        external_id=external_id,
        title=f"PMI {external_id}",
        content=f"制造业采购经理指数（PMI）：49.2%（{external_id}）",
        published_at=published_at,
        validity_profile=ValidityProfile.MONTHLY_MACRO_INDICATOR,
        validity_key=validity_key,
    )
    return SignalIngestion(
        source=source,
        signal=signal,
        fingerprint=fingerprint,
        collected_at=collected_at,
        authority=LifecycleAuthority("adapter", "test-adapter"),
    )


def _active_count(db_session: Session, source: DataSource) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .where(
            RawSignal.source_id == source.id,
            RawSignal.validity_state == ValidityState.ACTIVE,
        )
    )


def test_advisory_lock_keys_when_computed_are_stable_int32() -> None:
    # Given / When
    key1 = advisory_key1(1_000_000)
    key2 = advisory_key2("stats-pmi")

    # Then
    assert key1 == 1_000_000
    assert -(2**31) <= key2 < 2**31
    # NFC 规范化：组合形式与分解形式产生相同键
    assert advisory_key2("é") == advisory_key2("e\u0301")


def test_advisory_key1_when_source_id_out_of_int32_range_raises_typed_error() -> None:
    # Given / When / Then
    with pytest.raises(SignalIngestionError) as error:
        advisory_key1(0)
    assert error.value.code == "source_id_out_of_int32_range"

    with pytest.raises(SignalIngestionError) as error:
        advisory_key1(2**31)
    assert error.value.code == "source_id_out_of_int32_range"


def test_supersession_when_newer_authority_supersedes_old_active(
    db_session: Session,
) -> None:
    # Given
    source = _supersession_source(db_session)
    old = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-08",
        published_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        fingerprint="fp-aug",
        collected_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
    )
    new = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-09",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        fingerprint="fp-sep",
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )

    # When
    assert persist_signal_ingestions(db_session, [old]) == 1
    assert persist_signal_ingestions(db_session, [new]) == 1

    # Then
    signals = list(
        db_session.scalars(
            select(RawSignal)
            .where(RawSignal.source_id == source.id)
            .order_by(RawSignal.valid_from)
        )
    )
    assert len(signals) == 2
    aug, sep = signals
    assert aug.validity_state == "superseded"
    assert aug.valid_until == aug.valid_from
    assert aug.validity_reason["code"] == "superseded_by_newer_version"
    assert sep.validity_state == "active"
    assert _active_count(db_session, source) == 1


def test_supersession_when_out_of_order_old_data_does_not_reverse_supersede(
    db_session: Session,
) -> None:
    # Given：新一期先到，旧一期后到（乱序）
    source = _supersession_source(db_session)
    new = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-09",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        fingerprint="fp-sep",
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    old = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-08",
        published_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        fingerprint="fp-aug",
        collected_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
    )

    # When
    assert persist_signal_ingestions(db_session, [new]) == 1
    assert persist_signal_ingestions(db_session, [old]) == 1

    # Then：旧数据不反向替代，新一期保持 active
    signals = list(
        db_session.scalars(
            select(RawSignal)
            .where(RawSignal.source_id == source.id)
            .order_by(RawSignal.valid_from)
        )
    )
    assert len(signals) == 2
    aug, sep = signals
    assert sep.validity_state == "active"
    assert aug.validity_state == "superseded"
    assert aug.valid_until == aug.valid_from
    assert _active_count(db_session, source) == 1


def test_supersession_when_same_authority_time_conflicts_by_fingerprint(
    db_session: Session,
) -> None:
    # Given：同权威时间，较大 fingerprint 先到，较小 fingerprint 后到
    source = _supersession_source(db_session)
    larger_first = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-09-a",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        fingerprint="fp-b",
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    smaller_later = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-09-b",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        fingerprint="fp-a",
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )

    # When
    assert persist_signal_ingestions(db_session, [larger_first]) == 1
    assert persist_signal_ingestions(db_session, [smaller_later]) == 1

    # Then：后到的较小 fingerprint 原子替换先到 winner
    signals = list(
        db_session.scalars(
            select(RawSignal)
            .where(RawSignal.source_id == source.id)
            .order_by(RawSignal.fingerprint)
        )
    )
    assert len(signals) == 2
    winner, conflicted = signals
    assert winner.fingerprint == "fp-a"
    assert winner.validity_state == "active"
    assert conflicted.fingerprint == "fp-b"
    assert conflicted.validity_state == "conflicted"
    assert conflicted.valid_until == conflicted.valid_from
    assert conflicted.validity_reason["code"] == "same_authority_time_conflict"
    assert _active_count(db_session, source) == 1


def test_supersession_when_duplicate_fingerprint_is_idempotent(
    db_session: Session,
) -> None:
    # Given
    source = _supersession_source(db_session)
    first = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-09",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        fingerprint="fp-same",
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    duplicate = _supersession_ingestion(
        source,
        external_id="stats-pmi-2026-09",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        fingerprint="fp-same",
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )

    # When
    assert persist_signal_ingestions(db_session, [first]) == 1
    assert persist_signal_ingestions(db_session, [duplicate]) == 0

    # Then：重复项按指纹幂等，旧版本保持 active
    count = db_session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .where(RawSignal.source_id == source.id)
    )
    assert count == 1
    assert _active_count(db_session, source) == 1
