"""成员状态机持久化集成：加载状态、评估、撤销、更新快照字段。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

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
from app.signals.models import (
    CollectionRun,
    DataSource,
    DataSourceAuditLog,
    RawSignal,
)
from app.signals.models import (
    SourceMemberState as SourceMemberStateRow,
)
from app.signals.sources import PullSourceAdapter
from app.signals.validity import ValidityMode, ValidityState


def revoke_member_signals(
    session: Session, source_id: int, member_keys: frozenset[str],
    reason: dict[str, object],
) -> int:
    """批量撤销指定成员关联的 active until_revoked 信号。"""
    targets = list(
        session.scalars(
            select(RawSignal).where(
                RawSignal.source_id == source_id,
                RawSignal.validity_state == ValidityState.ACTIVE,
                RawSignal.validity_mode == ValidityMode.UNTIL_REVOKED,
                RawSignal.external_id.in_(member_keys),
            ).with_for_update()
        )
    )
    if not targets:
        return 0
    now = datetime.now(UTC)
    for target in targets:
        target.validity_state = ValidityState.REVOKED
        target.valid_until = now
        target.lifecycle_action = "revoke"
        target.validity_reason = reason
    session.add(
        DataSourceAuditLog(
            source_id=source_id,
            action="membership_revoke",
            actor_role="system",
            actor_id="snapshot_membership_evaluator",
            changes={
                "revoked_external_ids": [t.external_id for t in targets],
                "reason_code": reason.get("code"),
                "count": len(targets),
            },
        )
    )
    session.flush()
    return len(targets)


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
    current_states = _load_states(session, source.id)
    baseline = _load_baseline(session, source.id, current_states)
    candidate = _load_candidate(session, source.id)
    consecutive_full = _count_consecutive_full(session, source.id)

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


def _load_states(
    session: Session, source_id: int,
) -> dict[str, SourceMemberState]:
    rows = session.scalars(
        select(SourceMemberStateRow).where(
            SourceMemberStateRow.source_id == source_id
        )
    )
    return {
        row.member_key: SourceMemberState(
            member_key=row.member_key,
            status=row.status,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            consecutive_missing=row.consecutive_missing,
            baseline_snapshot_hash=SnapshotHash(row.baseline_snapshot_hash),
            quarantine_round=row.quarantine_round,
        )
        for row in rows
    }


def _load_baseline(
    session: Session, source_id: int,
    states: dict[str, SourceMemberState],
) -> CredibilityBaseline | None:
    run = session.scalars(
        select(CollectionRun)
        .where(
            CollectionRun.source_id == source_id,
            CollectionRun.snapshot_complete.is_(True),
            CollectionRun.snapshot_hash.is_not(None),
            CollectionRun.quarantine_round == 0,
        )
        .order_by(CollectionRun.finished_at.desc())
        .limit(1)
    ).first()
    if run is None or run.snapshot_hash is None or run.finished_at is None:
        return None
    return CredibilityBaseline(
        members=frozenset(
            k for k, s in states.items() if s.status == "active"
        ),
        count=run.member_count,
        snapshot_hash=SnapshotHash(run.snapshot_hash),
        captured_at=run.finished_at,
    )


def _load_candidate(
    session: Session, source_id: int,
) -> QuarantineCandidate | None:
    run = session.scalars(
        select(CollectionRun)
        .where(
            CollectionRun.source_id == source_id,
            CollectionRun.quarantine_round > 0,
        )
        .order_by(CollectionRun.finished_at.desc())
        .limit(1)
    ).first()
    if run is None or run.summary is None:
        return None
    summary = run.summary
    rounds = summary.get("quarantine_rounds")
    counts = summary.get("counts")
    missing = summary.get("missing_members")
    baseline_hash = summary.get("baseline_hash")
    baseline_count = summary.get("baseline_count")
    created_at = summary.get("created_at")
    if not (
        isinstance(rounds, list) and isinstance(counts, list)
        and isinstance(missing, list) and isinstance(baseline_hash, str)
        and isinstance(baseline_count, int) and isinstance(created_at, str)
    ):
        return None
    return QuarantineCandidate(
        missing_members=frozenset(missing),
        baseline_count=baseline_count,
        baseline_hash=SnapshotHash(baseline_hash),
        quarantine_rounds=[SnapshotHash(h) for h in rounds],
        counts=counts,
        created_at=datetime.fromisoformat(created_at),
    )


def _count_consecutive_full(session: Session, source_id: int) -> int:
    runs = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.source_id == source_id)
        .order_by(CollectionRun.finished_at.desc())
        .limit(50)
    )
    count = 0
    for run in runs:
        if run.snapshot_complete:
            count += 1
        else:
            break
    return count


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