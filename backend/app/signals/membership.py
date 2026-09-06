"""until_revoked 完整快照成员状态机。

仅当信源声明 authoritative_full_snapshot=true、连续两次成功完整快照均缺失
某成员、且本次条目数不低于上次可信完整快照的 80% 时，才撤销该成员。

大幅缩减隔离（>20%）：进入 quarantined candidate，后续质量基准改为候选快照；
连续三次完整快照相对原可信基线的缺失成员集合一致且相邻候选计数波动不超过
2%（相对于可信基线）时，第三次自动提升候选为新可信基线并撤销三轮均缺失的
成员；任一条件中断则重启候选计数并保持成员有效。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

_COUNT_DEVIATION_THRESHOLD: Final = 0.02
_STANDARD_RECOVERY_THRESHOLD: Final = 0.80


class SnapshotHash(str):
    """成员集合的稳定 SHA-256 摘要（按 sorted keys 计算）。"""


class EvalDecision(StrEnum):
    NO_ACTION = "no_action"
    REVOKE = "revoke"
    QUARANTINE_CANDIDATE = "quarantine_candidate"
    QUARANTINE_PROMOTED = "quarantine_promoted"


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    """一次完整快照的成员数据。"""

    keys: frozenset[str]
    count: int
    snapshot_complete: bool
    authoritative_full_snapshot: bool


@dataclass(frozen=True, slots=True)
class CredibilityBaseline:
    """可信完整快照基线。"""

    members: frozenset[str]
    count: int
    snapshot_hash: SnapshotHash
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class QuarantineCandidate:
    """大幅缩减后的候选快照状态。"""

    missing_members: frozenset[str]
    baseline_count: int
    baseline_hash: SnapshotHash
    quarantine_rounds: list[SnapshotHash]
    counts: list[int]
    created_at: datetime


@dataclass(slots=True)
class SourceMemberState:
    """单个名单成员的跟踪状态。"""

    member_key: str
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    consecutive_missing: int
    baseline_snapshot_hash: SnapshotHash
    quarantine_round: int


@dataclass(frozen=True, slots=True)
class RevokeDecision:
    """撤销决策。"""

    member_key: str
    source_id: int | None
    snapshot_hash: SnapshotHash
    reason: dict[str, object]


@dataclass(frozen=True, slots=True)
class EvalResult:
    """评估结果。"""

    decision: EvalDecision
    members_to_revoke: frozenset[str]
    revoke_reason: dict[str, object] | None
    revoke_decisions: list[RevokeDecision]
    quarantine_candidate: QuarantineCandidate | None
    source_audit: dict[str, object] | None
    updated_baseline: CredibilityBaseline | None


# ── 工具函数 ────────────────────────────────────────────────────────


def membership_keys_hash(keys: frozenset[str]) -> SnapshotHash:
    """成员键集合的稳定 SHA-256 摘要。"""
    canonical = ",".join(sorted(keys))
    return SnapshotHash(
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )


def _no_action() -> EvalResult:
    return EvalResult(
        decision=EvalDecision.NO_ACTION,
        members_to_revoke=frozenset(),
        revoke_reason=None,
        revoke_decisions=[],
        quarantine_candidate=None,
        source_audit=None,
        updated_baseline=None,
    )


def _count_deviation_exceeds(
    count: int, last_count: int, baseline_count: int
) -> bool:
    """相邻候选计数波动超过基线 _COUNT_DEVIATION_THRESHOLD 即判定不稳定。"""
    return abs(count - last_count) > baseline_count * _COUNT_DEVIATION_THRESHOLD


# ── 核心评估 ────────────────────────────────────────────────────────


def evaluate_membership_completeness(
    *,
    snapshot: SnapshotInput,
    current_states: dict[str, SourceMemberState],
    last_credible_baseline: CredibilityBaseline | None,
    now_utc: datetime,
    quarantine_candidate: QuarantineCandidate | None = None,
    consecutive_successful_full: int = 0,
) -> EvalResult:
    """评估一次完整快照，决定是否需要撤销成员。"""
    if not snapshot.snapshot_complete or not snapshot.authoritative_full_snapshot:
        return _no_action()

    # 空快照门禁：空响应/零条目绝不进入撤销或可提升隔离，也不得成为
    # 可信/候选基线（updated_baseline 与 quarantine_candidate 均为 None）。
    if not snapshot.keys or snapshot.count == 0:
        return _no_action()

    if last_credible_baseline is None:
        return _first_snapshot_result(snapshot, now_utc)

    assert snapshot.count >= 0
    snapshot_hash = membership_keys_hash(snapshot.keys)

    # 恢复检查：上次缺失的成员重新出现时清除隔离候选。
    if quarantine_candidate is not None:
        recovered = quarantine_candidate.missing_members - snapshot.keys
        if not recovered:
            quarantine_candidate = None

    # 大幅缩减路径：条目数低于可信基线 80%
    if snapshot.count < last_credible_baseline.count * _STANDARD_RECOVERY_THRESHOLD:
        return _quarantine_path(
            snapshot, snapshot_hash, last_credible_baseline,
            quarantine_candidate, now_utc,
        )

    # 标准路径：连续两次可信快照缺失 + 条目数 ≥ 80%
    revokers = _find_standard_revokers(
        snapshot.keys, last_credible_baseline, current_states,
        snapshot.count, snapshot_hash,
    )

    return EvalResult(
        decision=EvalDecision.REVOKE if revokers else EvalDecision.NO_ACTION,
        members_to_revoke=frozenset(r.member_key for r in revokers),
        revoke_reason=revokers[0].reason if revokers else None,
        revoke_decisions=revokers,
        quarantine_candidate=None,
        source_audit=None,
        updated_baseline=CredibilityBaseline(
            members=snapshot.keys,
            count=snapshot.count,
            snapshot_hash=snapshot_hash,
            captured_at=now_utc,
        ),
    )


# ── 首次快照 ────────────────────────────────────────────────────────


def _first_snapshot_result(
    snapshot: SnapshotInput, now_utc: datetime,
) -> EvalResult:
    snapshot_hash = membership_keys_hash(snapshot.keys)
    return EvalResult(
        decision=EvalDecision.NO_ACTION,
        members_to_revoke=frozenset(),
        revoke_reason=None,
        revoke_decisions=[],
        quarantine_candidate=None,
        source_audit=None,
        updated_baseline=CredibilityBaseline(
            members=snapshot.keys,
            count=snapshot.count,
            snapshot_hash=snapshot_hash,
            captured_at=now_utc,
        ),
    )


# ── 大幅缩减隔离 ────────────────────────────────────────────────────


def _quarantine_path(
    snapshot: SnapshotInput,
    snapshot_hash: SnapshotHash,
    baseline: CredibilityBaseline,
    candidate: QuarantineCandidate | None,
    now_utc: datetime,
) -> EvalResult:
    """大幅缩减路径：无候选则进入隔离，有候选则检查三轮稳定性。"""
    if candidate is None:
        missing = baseline.members - snapshot.keys
        return EvalResult(
            decision=EvalDecision.QUARANTINE_CANDIDATE,
            members_to_revoke=frozenset(),
            revoke_reason=None,
            revoke_decisions=[],
            quarantine_candidate=QuarantineCandidate(
                missing_members=missing,
                baseline_count=baseline.count,
                baseline_hash=baseline.snapshot_hash,
                quarantine_rounds=[snapshot_hash],
                counts=[snapshot.count],
                created_at=now_utc,
            ),
            source_audit={
                "action": "quarantine_entered",
                "missing_count": len(missing),
                "count": snapshot.count,
                "baseline_count": baseline.count,
            },
            updated_baseline=None,
        )

    current_missing = candidate.missing_members - snapshot.keys
    if current_missing != candidate.missing_members:
        # 缺失集合变化：重置候选
        return EvalResult(
            decision=EvalDecision.NO_ACTION,
            members_to_revoke=frozenset(),
            revoke_reason=None,
            revoke_decisions=[],
            quarantine_candidate=None,
            source_audit={
                "action": "quarantine_reset", "reason": "missing_set_changed",
            },
            updated_baseline=None,
        )
    if _count_deviation_exceeds(
        snapshot.count, candidate.counts[-1], candidate.baseline_count,
    ):
        # 计数波动：重置候选
        return EvalResult(
            decision=EvalDecision.NO_ACTION,
            members_to_revoke=frozenset(),
            revoke_reason=None,
            revoke_decisions=[],
            quarantine_candidate=None,
            source_audit={
                "action": "quarantine_reset", "reason": "count_instability",
            },
            updated_baseline=None,
        )
    if len(candidate.quarantine_rounds) >= 2:
        # 三轮稳定：提升候选为新基线并撤销三轮均缺失成员
        rounds = list(candidate.quarantine_rounds) + [snapshot_hash]
        reason: dict[str, object] = {
            "code": "quarantine_promoted",
            "anchor_source": "collected_at",
            "details": {
                "quarantine_rounds": len(rounds),
                "missing_members": sorted(candidate.missing_members),
                "baseline_count": candidate.baseline_count,
                "final_count": snapshot.count,
            },
        }
        revoke_decisions = [
            RevokeDecision(
                member_key=k, source_id=None,
                snapshot_hash=snapshot_hash, reason=reason,
            )
            for k in candidate.missing_members
        ]
        return EvalResult(
            decision=EvalDecision.QUARANTINE_PROMOTED,
            members_to_revoke=candidate.missing_members,
            revoke_reason=reason,
            revoke_decisions=revoke_decisions,
            quarantine_candidate=None,
            source_audit={
                "action": "quarantine_promoted",
                "rounds": len(rounds),
                "revoked_count": len(candidate.missing_members),
            },
            updated_baseline=CredibilityBaseline(
                members=snapshot.keys,
                count=snapshot.count,
                snapshot_hash=snapshot_hash,
                captured_at=now_utc,
            ),
        )

    # 轮次不足：继续累积候选
    return EvalResult(
        decision=EvalDecision.QUARANTINE_CANDIDATE,
        members_to_revoke=frozenset(),
        revoke_reason=None,
        revoke_decisions=[],
        quarantine_candidate=QuarantineCandidate(
            missing_members=candidate.missing_members,
            baseline_count=candidate.baseline_count,
            baseline_hash=candidate.baseline_hash,
            quarantine_rounds=list(candidate.quarantine_rounds) + [snapshot_hash],
            counts=list(candidate.counts) + [snapshot.count],
            created_at=candidate.created_at,
        ),
        source_audit={
            "action": "quarantine_continued",
            "round": len(candidate.quarantine_rounds) + 1,
        },
        updated_baseline=None,
    )


# ── 标准撤销 ────────────────────────────────────────────────────────


def _find_standard_revokers(
    snapshot_keys: frozenset[str],
    baseline: CredibilityBaseline,
    current_states: dict[str, SourceMemberState],
    count: int,
    snapshot_hash: SnapshotHash,
) -> list[RevokeDecision]:
    """标准路径：连续两次可信缺失 + count ≥ 80% 基线时返回撤销决策。

    撤销依据是每个成员自身的连续缺失计数：本次快照缺失某成员时其
    consecutive_missing 递增，达到 2（即连续两次可信快照均缺失）才撤销。
    """
    if count < baseline.count * _STANDARD_RECOVERY_THRESHOLD:
        return []
    missing_from_baseline = baseline.members - snapshot_keys
    if not missing_from_baseline:
        return []
    revokers: list[RevokeDecision] = []
    for member_key in missing_from_baseline:
        state = current_states.get(member_key)
        consecutive_missing = (state.consecutive_missing if state is not None else 0) + 1
        if consecutive_missing < 2:
            continue
        revokers.append(
            RevokeDecision(
                member_key=member_key,
                source_id=None,
                snapshot_hash=snapshot_hash,
                reason={
                    "code": "two_consecutive_misses",
                    "anchor_source": "collected_at",
                    "details": {
                        "missing_member": member_key,
                        "consecutive_missing": consecutive_missing,
                        "baseline_count": baseline.count,
                        "snapshot_count": count,
                    },
                },
            )
        )
    return revokers


# ── 撤销持久化 ──────────────────────────────────────────────────────
# 持久化集成（revoke_member_signals / apply_membership_snapshot）见
# membership_store.py，本模块保持纯逻辑，便于无数据库单测。
