"""until_revoked 完整快照成员状态机：纯逻辑测试。

覆盖：
- 正常撤销路径：连续两次可信缺失 → 第三次采集时撤销
- 新成员加入不影响现有成员
- 大幅缩减 (>20%) 进入隔离候选
- 隔离候选三轮稳定后自动提升
- 会员从上次缺失恢复时清除隔离候选
- 不稳定缺失集合不触发提升
- 空快照、部分分页不撤销
"""

from datetime import UTC, datetime

from app.signals.membership import (
    CredibilityBaseline,
    EvalDecision,
    QuarantineCandidate,
    SnapshotHash,
    SnapshotInput,
    SourceMemberState,
    evaluate_membership_completeness,
    membership_keys_hash,
)

NOW_UTC = datetime(2026, 9, 6, 12, tzinfo=UTC)

_BASELINE_KEYS = frozenset(
    f"entity-{c}" for c in "ABCDEFGHIJ"
)
_BASELINE_SNAPSHOT = SnapshotInput(
    keys=frozenset(_BASELINE_KEYS),
    count=100,
    snapshot_complete=True,
    authoritative_full_snapshot=True,
)


def _state(member_key: str, **overrides: object) -> SourceMemberState:
    defaults: dict[str, object] = {
        "member_key": member_key,
        "status": "active",
        "first_seen_at": NOW_UTC,
        "last_seen_at": NOW_UTC,
        "consecutive_missing": 0,
        "baseline_snapshot_hash": SnapshotHash("hash"),
        "quarantine_round": 0,
    }
    defaults.update(overrides)
    return SourceMemberState(**defaults)  # type: ignore[arg-type]


def _baseline(
    members: frozenset[str] = _BASELINE_KEYS, count: int = 100
) -> CredibilityBaseline:
    return CredibilityBaseline(
        members=members,
        count=count,
        snapshot_hash=membership_keys_hash(members),
        captured_at=NOW_UTC,
    )


def _evaluate(
    snapshot: SnapshotInput,
    current_states: dict[str, SourceMemberState],
    baseline: CredibilityBaseline | None,
    quarantine: QuarantineCandidate | None = None,
    consecutive_full: int = 1,
) -> EvalDecision:
    """辅助：以冻结时钟执行单次评估。"""
    return evaluate_membership_completeness(
        snapshot=snapshot,
        current_states=current_states,
        last_credible_baseline=baseline,
        now_utc=NOW_UTC,
        quarantine_candidate=quarantine,
        consecutive_successful_full=consecutive_full,
    )


# ─── 正常路径 ───────────────────────────────────────────────────────


class TestNormalRevocation:
    """可信基线 {A..J}，两轮缩减到 {A..I}，J 在第三次采集时撤销。"""

    def test_first_missing_not_revoked(self) -> None:
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHI"),
            count=90,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline(), consecutive_full=2)
        assert len(result.members_to_revoke) == 0

    def test_second_consecutive_missing_revokes(self) -> None:
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        states["entity-J"] = _state(
            "entity-J", consecutive_missing=1, last_seen_at=NOW_UTC
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHI"),
            count=90,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline(), consecutive_full=3)
        assert result.decision is EvalDecision.REVOKE
        assert result.members_to_revoke == frozenset({"entity-J"})
        assert result.revoke_reason is not None
        assert "two_consecutive_misses" in result.revoke_reason["code"]

    def test_new_member_added(self) -> None:
        keys = frozenset(f"entity-{c}" for c in "ABCDEFGHIJK")
        snap = SnapshotInput(
            keys=keys, count=110,
            snapshot_complete=True, authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, {_k: _state(_k) for _k in _BASELINE_KEYS}, _baseline())
        assert len(result.members_to_revoke) == 0

    def test_baseline_updated_on_success(self) -> None:
        keys9 = frozenset(f"entity-{c}" for c in "ABCDEFGHI")
        snap = SnapshotInput(
            keys=keys9, count=90,
            snapshot_complete=True, authoritative_full_snapshot=True,
        )
        result = _evaluate(
            snap, {_k: _state(_k) for _k in _BASELINE_KEYS},
            _baseline(), consecutive_full=2,
        )
        assert result.updated_baseline is not None
        assert result.updated_baseline.members == keys9
        assert result.updated_baseline.count == 90


# ─── 隔离候选 ───────────────────────────────────────────────────────


class TestQuarantinePromotion:
    """可信基线 100 → 缩减至 60 以下触发隔离 → 三轮稳定 → 提升撤销。"""

    def test_large_reduction_enters_quarantine(self) -> None:
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        keys6 = frozenset(f"entity-{c}" for c in "ABCDEF")
        snap = SnapshotInput(
            keys=keys6, count=60,
            snapshot_complete=True, authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline())
        assert result.decision is EvalDecision.QUARANTINE_CANDIDATE
        assert result.quarantine_candidate is not None
        assert result.quarantine_candidate.missing_members == frozenset(
            f"entity-{c}" for c in "GHIJ"
        )
        assert len(result.members_to_revoke) == 0

    def test_three_stable_rounds_promotes(self) -> None:
        missing = frozenset(f"entity-{c}" for c in "GHIJ")
        candidate = QuarantineCandidate(
            missing_members=missing,
            baseline_count=100,
            baseline_hash=membership_keys_hash(_BASELINE_KEYS),
            quarantine_rounds=[
                SnapshotHash("q1"), SnapshotHash("q2"),
            ],
            counts=[60, 61],
            created_at=NOW_UTC,
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEF"),
            count=60,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        result = _evaluate(snap, states, _baseline(), quarantine=candidate)
        assert result.decision is EvalDecision.QUARANTINE_PROMOTED
        assert result.members_to_revoke == missing
        assert "quarantine_promoted" in result.revoke_reason["code"]

    def test_unstable_missing_not_promoted(self) -> None:
        missing1 = frozenset(f"entity-{c}" for c in "GHIJ")
        candidate = QuarantineCandidate(
            missing_members=missing1,
            baseline_count=100,
            baseline_hash=membership_keys_hash(_BASELINE_KEYS),
            quarantine_rounds=[SnapshotHash("q1"), SnapshotHash("q2")],
            counts=[60, 61],
            created_at=NOW_UTC,
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDE"),
            count=55,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        result = _evaluate(snap, states, _baseline(), quarantine=candidate)
        assert result.decision is EvalDecision.NO_ACTION
        assert len(result.members_to_revoke) == 0
        assert result.quarantine_candidate is None


# ─── 恢复 ───────────────────────────────────────────────────────────


class TestMemberRecovery:
    """缺失成员重新出现 → 清除隔离候选。"""

    def test_recovery_clears_quarantine(self) -> None:
        missing = frozenset(f"entity-{c}" for c in "GHIJ")
        candidate = QuarantineCandidate(
            missing_members=missing,
            baseline_count=100,
            baseline_hash=membership_keys_hash(_BASELINE_KEYS),
            quarantine_rounds=[SnapshotHash("q1")],
            counts=[60],
            created_at=NOW_UTC,
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHIJ"),
            count=100,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        result = _evaluate(snap, states, _baseline(), quarantine=candidate)
        assert result.quarantine_candidate is None
        assert len(result.members_to_revoke) == 0

    def test_count_deviation_rejects_promotion(self) -> None:
        missing = frozenset(f"entity-{c}" for c in "GHIJ")
        candidate = QuarantineCandidate(
            missing_members=missing,
            baseline_count=100,
            baseline_hash=membership_keys_hash(_BASELINE_KEYS),
            quarantine_rounds=[SnapshotHash("q1"), SnapshotHash("q2")],
            counts=[60, 63],
            created_at=NOW_UTC,
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEF"),
            count=60,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        result = _evaluate(snap, states, _baseline(), quarantine=candidate)
        assert result.decision is EvalDecision.NO_ACTION
        assert result.quarantine_candidate is None


# ─── 安全门禁：不应触发撤销 ──────────────────────────────────────────


class TestSafetyGates:
    """确保各种异常情况不批量撤销成员。"""

    def test_empty_snapshot_no_revoke(self) -> None:
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(), count=0,
            snapshot_complete=False, authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline())
        assert len(result.members_to_revoke) == 0
        assert result.decision is EvalDecision.NO_ACTION

    def test_partial_pagination_no_revoke(self) -> None:
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDE"),
            count=50,
            snapshot_complete=False,
            authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline())
        assert len(result.members_to_revoke) == 0

    def test_non_authoritative_no_revoke(self) -> None:
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHI"),
            count=90,
            snapshot_complete=True,
            authoritative_full_snapshot=False,
        )
        result = _evaluate(snap, states, _baseline())
        assert len(result.members_to_revoke) == 0

    def test_non_authoritative_second_missing_never_revokes(self) -> None:
        """非权威快照即使 snapshot_complete=True 且成员已连续缺失两轮也不撤销。

        危险路径：authoritative_full_snapshot=False 但 snapshot_complete=True，
        成员 consecutive_missing=1（已缺失一轮），本次再缺失即达两轮。
        """
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        states["entity-J"] = _state(
            "entity-J", consecutive_missing=1, last_seen_at=NOW_UTC
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHI"),
            count=90,
            snapshot_complete=True,
            authoritative_full_snapshot=False,
        )
        result = evaluate_membership_completeness(
            snapshot=snap,
            current_states=states,
            last_credible_baseline=_baseline(),
            now_utc=NOW_UTC,
            consecutive_successful_full=3,
        )
        assert result.decision is EvalDecision.NO_ACTION
        assert len(result.members_to_revoke) == 0
        assert result.updated_baseline is None

    def test_authoritative_empty_snapshot_never_quarantines(self) -> None:
        """authoritative 空快照即使有可信基线也绝不进入可提升隔离、绝不撤销。

        危险路径：keys 为空且 count==0 但 snapshot_complete=True，
        当前实现会走大幅缩减隔离路径并在三轮后提升撤销全部成员。
        """
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(), count=0,
            snapshot_complete=True, authoritative_full_snapshot=True,
        )
        result = evaluate_membership_completeness(
            snapshot=snap,
            current_states=states,
            last_credible_baseline=_baseline(),
            now_utc=NOW_UTC,
            consecutive_successful_full=3,
        )
        assert result.decision is EvalDecision.NO_ACTION
        assert len(result.members_to_revoke) == 0
        assert result.quarantine_candidate is None
        assert result.updated_baseline is None

    def test_authoritative_empty_first_snapshot_never_becomes_baseline(self) -> None:
        """首次快照即为空时不得成为可信基线。"""
        snap = SnapshotInput(
            keys=frozenset(), count=0,
            snapshot_complete=True, authoritative_full_snapshot=True,
        )
        result = evaluate_membership_completeness(
            snapshot=snap,
            current_states={},
            last_credible_baseline=None,
            now_utc=NOW_UTC,
        )
        assert result.decision is EvalDecision.NO_ACTION
        assert result.updated_baseline is None

    def test_no_previous_snapshot_no_revoke(self) -> None:
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHI"),
            count=90,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, {}, None)
        assert len(result.members_to_revoke) == 0
        assert result.updated_baseline is not None

    def test_count_below_80_percent_no_revoke(self) -> None:
        """即使只有 J 缺失，但 count 低于基线 80% 时不标准撤销。"""
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDEFGHI"),
            count=70,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline(), consecutive_full=2)
        assert len(result.members_to_revoke) == 0
        assert result.decision is EvalDecision.QUARANTINE_CANDIDATE

    def test_scan_failure_no_revoke(self) -> None:
        """采集失败时 snapshot_complete=False，不应撤销。"""
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        snap = SnapshotInput(
            keys=frozenset(), count=0,
            snapshot_complete=False, authoritative_full_snapshot=True,
        )
        result = _evaluate(snap, states, _baseline())
        assert len(result.members_to_revoke) == 0

    def test_unstable_quarantine_candidates_not_promoted(self) -> None:
        """连续三轮缺失集合不一致时，隔离候选不提升。"""
        missing1 = frozenset(f"entity-{c}" for c in "GHIJ")
        candidate = QuarantineCandidate(
            missing_members=missing1,
            baseline_count=100,
            baseline_hash=membership_keys_hash(_BASELINE_KEYS),
            quarantine_rounds=[SnapshotHash("q1"), SnapshotHash("q2")],
            counts=[60, 61],
            created_at=NOW_UTC,
        )
        snap = SnapshotInput(
            keys=frozenset(f"entity-{c}" for c in "ABCDE"),
            count=55,
            snapshot_complete=True,
            authoritative_full_snapshot=True,
        )
        states = {_k: _state(_k) for _k in _BASELINE_KEYS}
        result = _evaluate(snap, states, _baseline(), quarantine=candidate)
        assert result.decision is EvalDecision.NO_ACTION
        assert len(result.members_to_revoke) == 0


class TestMembershipKeysHash:
    """snapshot_hash 序列稳定且成员顺序无关。"""

    def test_order_independent(self) -> None:
        h1 = membership_keys_hash(frozenset({"a", "b", "c"}))
        h2 = membership_keys_hash(frozenset({"c", "a", "b"}))
        assert h1 == h2

    def test_different_sets_different_hash(self) -> None:
        h1 = membership_keys_hash(frozenset({"a", "b"}))
        h2 = membership_keys_hash(frozenset({"a", "c"}))
        assert h1 != h2

    def test_empty_set(self) -> None:
        h = membership_keys_hash(frozenset())
        assert isinstance(h, SnapshotHash)
        assert len(h) > 0
