"""until_revoked 完整快照成员状态机：持久化读路径与撤销。

负责从数据库加载成员状态、可信基线、隔离候选与连续完整快照计数，
以及批量撤销 active until_revoked 信号并写入审计日志。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.membership_types import (
    CredibilityBaseline,
    QuarantineCandidate,
    SnapshotHash,
    SourceMemberState,
)
from app.signals.models import (
    CollectionRun,
    DataSourceAuditLog,
    RawSignal,
)
from app.signals.models import (
    SourceMemberState as SourceMemberStateRow,
)
from app.signals.validity import ValidityMode, ValidityState


def revoke_member_signals(
    session: Session, source_id: int, member_keys: frozenset[str],
    reason: dict[str, object], *, now_utc: datetime,
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
    for target in targets:
        target.validity_state = ValidityState.REVOKED
        target.valid_until = now_utc
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


def load_states(
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


def load_baseline(
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
            CollectionRun.snapshot_quality != "empty",
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


def load_candidate(
    session: Session, source_id: int,
) -> QuarantineCandidate | None:
    run = session.scalars(
        select(CollectionRun)
        .where(
            CollectionRun.source_id == source_id,
            CollectionRun.quarantine_round > 0,
            CollectionRun.snapshot_quality != "empty",
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


def count_consecutive_full(session: Session, source_id: int) -> int:
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
