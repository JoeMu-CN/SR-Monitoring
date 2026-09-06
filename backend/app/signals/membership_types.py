"""until_revoked 完整快照成员状态机：共享类型与工具函数。

纯逻辑类型与哈希/判定工具，供评估器与持久化层复用，不依赖数据库。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

COUNT_DEVIATION_THRESHOLD: Final = 0.02
STANDARD_RECOVERY_THRESHOLD: Final = 0.80


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


def membership_keys_hash(keys: frozenset[str]) -> SnapshotHash:
    """成员键集合的稳定 SHA-256 摘要。"""
    canonical = ",".join(sorted(keys))
    return SnapshotHash(
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )


def no_action_result() -> EvalResult:
    """构造无操作评估结果（非权威/非完整/空快照门禁共用）。"""
    return EvalResult(
        decision=EvalDecision.NO_ACTION,
        members_to_revoke=frozenset(),
        revoke_reason=None,
        revoke_decisions=[],
        quarantine_candidate=None,
        source_audit=None,
        updated_baseline=None,
    )


def count_deviation_exceeds(
    count: int, last_count: int, baseline_count: int,
) -> bool:
    """相邻候选计数波动超过基线 COUNT_DEVIATION_THRESHOLD 即判定不稳定。"""
    return abs(count - last_count) > baseline_count * COUNT_DEVIATION_THRESHOLD