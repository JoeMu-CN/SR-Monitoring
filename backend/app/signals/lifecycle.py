"""可信结构化信号生命周期动作的原子数据库变更。"""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.models import DataSourceAuditLog, RawSignal
from app.signals.validity import LifecycleAction, ValidityState

if TYPE_CHECKING:
    from app.signals.ingestion import SignalIngestion, SignalValiditySnapshot


def apply_lifecycle_action(
    session: Session,
    ingestion: SignalIngestion,
    snapshot: SignalValiditySnapshot,
) -> None:
    """锁定同信源目标，并在当前入库事务内应用可信动作与审计。"""
    action = ingestion.signal.lifecycle_action
    if action is LifecycleAction.ASSERT:
        return
    query = select(RawSignal).where(RawSignal.source_id == ingestion.source.id)
    if ingestion.signal.target_signal_id is not None:
        query = query.where(RawSignal.id == ingestion.signal.target_signal_id)
    elif ingestion.signal.validity_key is not None:
        query = (
            query.where(
                RawSignal.validity_key == ingestion.signal.validity_key,
                RawSignal.validity_state == ValidityState.ACTIVE,
            )
            .order_by(RawSignal.valid_from.desc(), RawSignal.id.desc())
            .limit(1)
        )
    target = session.scalar(query.with_for_update())
    if target is None:
        from app.signals.ingestion import SignalIngestionError

        raise SignalIngestionError("lifecycle_target_not_found")
    if target.validity_state != ValidityState.ACTIVE:
        from app.signals.ingestion import SignalIngestionError

        raise SignalIngestionError("lifecycle_target_not_active")
    match action:
        case LifecycleAction.CONFIRM:
            target.review_due_at = snapshot.review_due_at
            if snapshot.valid_until is not None:
                target.valid_until = snapshot.valid_until
        case LifecycleAction.REVOKE:
            target.validity_state = ValidityState.REVOKED
            target.valid_until = max(
                target.valid_from or snapshot.valid_from,
                snapshot.valid_from,
            )
        case LifecycleAction.SUPERSEDE:
            target.validity_state = ValidityState.SUPERSEDED
            target.valid_until = max(
                target.valid_from or snapshot.valid_from,
                snapshot.valid_from,
            )
        case LifecycleAction.ASSERT:
            return
        case unreachable:
            assert_never(unreachable)
    target.lifecycle_action = action
    target.validity_reason = {
        "code": f"lifecycle_{action.value}",
        "anchor_source": snapshot.reason["anchor_source"],
        "details": {
            "action_signal_external_id": ingestion.signal.external_id,
            "actor_type": ingestion.authority.role,
            "reason": ingestion.signal.lifecycle_reason,
        },
    }
    session.add(
        DataSourceAuditLog(
            source_id=ingestion.source.id,
            action=f"signal_{action.value}",
            actor_role=ingestion.authority.role,
            actor_id=ingestion.authority.actor_id,
            changes={
                "target_signal_id": target.id,
                "lifecycle_action": action.value,
                "reason": ingestion.signal.lifecycle_reason,
            },
        )
    )
