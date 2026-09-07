"""until_revoked 完整快照成员状态机：持久化写路径。

在采集事务内评估并应用成员状态机：更新成员状态行、采集运行记录，
并返回撤销成员数。评估逻辑见 membership_evaluator，读路径见
membership_repository。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.membership_evaluator import evaluate_membership_completeness
from app.signals.membership_repository import (
    count_consecutive_full,
    load_baseline,
    load_candidate,
    load_states,
    revoke_member_signals,
)
from app.signals.membership_types import (
    EvalDecision,
    SnapshotHash,
    SnapshotInput,
    membership_keys_hash,
)
from app.signals.models import (
    CollectionRun,
    DataSource,
)
from app.signals.models import (
    SourceMemberState as SourceMemberStateRow,
)
from app.signals.sources import PullSourceAdapter


def apply_membership_snapshot(
    session: Session,
    source: DataSource,
    adapter: PullSourceAdapter,
    run: CollectionRun,
    snapshot_keys: frozenset[str],
    snapshot_count: int,
    now_utc: datetime,
) -> int:
    """在采集事务内评估并应用成员状态机，返回撤销成员数。"""
    snapshot_complete = bool(getattr(adapter, "authoritative_full_snapshot", False))
    snapshot_hash = membership_keys_hash(snapshot_keys)
    current_states = load_states(session, source.id)
    baseline = load_baseline(session, source.id, current_states)
    candidate = load_candidate(session, source.id)
    consecutive_full = count_consecutive_full(session, source.id)

    result = evaluate_membership_completeness(
        snapshot=SnapshotInput(
            keys=snapshot_keys,
            count=snapshot_count,
            snapshot_complete=snapshot_complete,
            authoritative_full_snapshot=snapshot_complete,
        ),
        current_states=current_states,
        last_credible_baseline=baseline,
        now_utc=now_utc,
        quarantine_candidate=candidate,
        consecutive_successful_full=consecutive_full,
    )

    revoked = 0
    if result.decision in (EvalDecision.REVOKE, EvalDecision.QUARANTINE_PROMOTED):
        revoked = revoke_member_signals(
            session, source.id, result.members_to_revoke, result.revoke_reason or {},
            now_utc=now_utc,
        )

    _upsert_states(
        session, source.id, snapshot_keys, snapshot_hash, now_utc,
        result.members_to_revoke,
    )
    _update_run(
        session, run, snapshot_complete, snapshot_hash, snapshot_count,
        len(snapshot_keys - set(current_states)), revoked, result,
    )
    return revoked


def _upsert_states(
    session: Session,
    source_id: int,
    snapshot_keys: frozenset[str],
    snapshot_hash: SnapshotHash,
    now_utc: datetime,
    revoked_keys: frozenset[str],
) -> None:
    existing = {
        row.member_key: row
        for row in session.scalars(
            select(SourceMemberStateRow).where(
                SourceMemberStateRow.source_id == source_id
            )
        )
    }
    for key in snapshot_keys:
        row = existing.get(key)
        if row is None:
            session.add(
                SourceMemberStateRow(
                    source_id=source_id,
                    member_key=key,
                    status="active",
                    first_seen_at=now_utc,
                    last_seen_at=now_utc,
                    consecutive_missing=0,
                    baseline_snapshot_hash=str(snapshot_hash),
                    quarantine_round=0,
                )
            )
        else:
            row.last_seen_at = now_utc
            row.consecutive_missing = 0
            row.baseline_snapshot_hash = str(snapshot_hash)
    # 本次快照缺失的 active 成员：连续缺失计数递增。
    for key, row in existing.items():
        if key not in snapshot_keys and row.status == "active":
            row.consecutive_missing += 1
    for key in revoked_keys:
        row = existing.get(key)
        if row is not None:
            row.status = "revoked"


def _update_run(
    session: Session,
    run: CollectionRun,
    snapshot_complete: bool,
    snapshot_hash: SnapshotHash,
    snapshot_count: int,
    new_member_count: int,
    revoked_count: int,
    result: object,
) -> None:
    run.snapshot_complete = snapshot_complete
    run.snapshot_hash = str(snapshot_hash)
    if snapshot_count == 0:
        run.snapshot_quality = "empty"
    else:
        run.snapshot_quality = "complete" if snapshot_complete else "partial"
    run.member_count = snapshot_count
    run.new_member_count = new_member_count
    run.revoked_member_count = revoked_count
    candidate = getattr(result, "quarantine_candidate", None)
    if candidate is not None:
        run.quarantine_round = len(candidate.quarantine_rounds)
        run.quarantine_baseline_hash = str(candidate.baseline_hash)
        run.quarantine_missing_hash = str(
            membership_keys_hash(candidate.missing_members)
        )
        run.summary = {
            "quarantine_rounds": [str(h) for h in candidate.quarantine_rounds],
            "counts": candidate.counts,
            "missing_members": sorted(candidate.missing_members),
            "baseline_count": candidate.baseline_count,
            "baseline_hash": str(candidate.baseline_hash),
            "created_at": candidate.created_at.isoformat(),
        }
    elif run.quarantine_round > 0:
        run.quarantine_round = 0
        run.quarantine_baseline_hash = None
        run.quarantine_missing_hash = None
        run.summary = None
    session.flush()
