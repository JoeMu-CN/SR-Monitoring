"""事件、提醒的有效证据聚合与风险信号有效性兼容入口。"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.risks.scoring import ScoringSettings
from app.risks.signal_validity import (
    InactiveRiskSignalError,
    classify_pending_signal,
    expire_signal_if_due,
    is_raw_signal_effective,
    mark_classification_failed,
    signal_validity_window,
)
from app.signals.models import RawSignal

__all__ = [
    "InactiveRiskSignalError",
    "classify_pending_signal",
    "expire_signal_if_due",
    "is_raw_signal_effective",
    "mark_classification_failed",
    "signal_validity_window",
]


@dataclass(frozen=True, slots=True)
class EventSupport:
    effective_count: int
    expires_at: datetime | None
    expiry_kind: str


@dataclass(frozen=True, slots=True)
class LifecycleRefreshResult:
    expired_signals: int
    expired_alerts: int
    refreshed_events: int


def _support_deadline(signal: RawSignal) -> datetime | None:
    deadlines = [
        value for value in (signal.valid_until, signal.review_due_at) if value is not None
    ]
    return min(deadlines) if deadlines else None


def refresh_event_support(
    session: Session, event: RiskEvent, *, now_utc: datetime
) -> EventSupport:
    """以一次注入时钟聚合事件当前有效证据并保存可解释快照。"""
    signals = list(
        session.scalars(
            select(RawSignal)
            .join(RiskEventSignal, RiskEventSignal.signal_id == RawSignal.id)
            .where(RiskEventSignal.event_id == event.id)
        )
    )
    for signal in signals:
        expire_signal_if_due(signal, now_utc=now_utc)
    effective = [signal for signal in signals if is_raw_signal_effective(signal, now_utc=now_utc)]
    if not effective:
        event.validity_state = "expired"
        event.review_due_at = None
        event.validity_reason = {
            "code": "no_effective_signal_support",
            "anchor_source": "published_at",
            "details": {"effective_count": 0},
        }
        return EventSupport(0, event.valid_until, "finite" if event.valid_until else "unbounded")
    deadlines = [_support_deadline(signal) for signal in effective]
    finite_deadlines = [value for value in deadlines if value is not None]
    expires_at = (
        None if len(finite_deadlines) != len(deadlines) else max(finite_deadlines)
    )
    review_deadlines = [
        signal.review_due_at for signal in effective if signal.review_due_at is not None
    ]
    versions = sorted(
        signal.validity_policy_version
        for signal in effective
        if signal.validity_policy_version is not None
    )
    event.validity_state = "active"
    event.valid_until = expires_at
    event.review_due_at = min(review_deadlines) if review_deadlines else None
    event.validity_policy_version = hashlib.sha256("|".join(versions).encode()).hexdigest()
    event.validity_reason = {
        "code": "effective_signal_support",
        "anchor_source": "published_at",
        "details": {"effective_count": len(effective)},
    }
    return EventSupport(
        len(effective), expires_at, "unbounded" if expires_at is None else "finite"
    )


def _refresh_event_alerts(
    session: Session, event: RiskEvent, *, now_utc: datetime
) -> int:
    support = refresh_event_support(session, event, now_utc=now_utc)
    alerts = list(
        session.scalars(
            select(RiskAlert)
            .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
            .where(
                SupplierEventMatch.event_id == event.id,
                RiskAlert.status == "current",
                RiskAlert.expiry_kind != "legacy",
            )
        )
    )
    expired = 0
    for alert in alerts:
        if support.effective_count == 0:
            alert.status = "expired"
            alert.updated_at = now_utc
            expired += 1
        else:
            alert.expires_at = support.expires_at
            alert.expiry_kind = support.expiry_kind
    return expired


def refresh_signal_events(
    session: Session, signal: RawSignal, *, now_utc: datetime
) -> int:
    """在信号生命周期事务内重算其关联事件和非 legacy 提醒。"""
    events = list(
        session.scalars(
            select(RiskEvent)
            .join(RiskEventSignal, RiskEventSignal.event_id == RiskEvent.id)
            .where(RiskEventSignal.signal_id == signal.id)
        )
    )
    for event in events:
        _refresh_event_alerts(session, event, now_utc=now_utc)
    return len(events)


def compute_alert_expires_at(
    event: RiskEvent,
    scoring: ScoringSettings | None = None,
    source_validity_days: int | None = None,
) -> datetime | None:
    """提醒只继承事件当前有效证据截止，不使用滚动 now。"""
    del scoring, source_validity_days
    return event.valid_until


def expire_alerts(session: Session, *, now_utc: datetime | None = None) -> int:
    """按 ``<=`` 边界失效提醒；legacy 只执行迁移前的已存截止逻辑。"""
    now = now_utc or datetime.now(UTC)
    legacy_alerts = list(
        session.scalars(
            select(RiskAlert).where(
                RiskAlert.status == "current",
                RiskAlert.expiry_kind == "legacy",
            )
        )
    )
    expired = 0
    for alert in legacy_alerts:
        if alert.expires_at is not None and alert.expires_at <= now:
            alert.status = "expired"
            alert.updated_at = now
            expired += 1
    events = list(
        session.scalars(
            select(RiskEvent)
            .join(SupplierEventMatch, SupplierEventMatch.event_id == RiskEvent.id)
            .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
            .where(
                RiskAlert.status == "current",
                RiskAlert.expiry_kind != "legacy",
            )
        ).unique()
    )
    for event in events:
        expired += _refresh_event_alerts(session, event, now_utc=now)
    return expired


def coordinate_risk_lifecycle(
    session: Session, *, now_utc: datetime
) -> LifecycleRefreshResult:
    """物化全部到期信号，并在同一事务协调事件与提醒状态。"""
    expired_signals = 0
    refreshed_events = 0
    signals = list(
        session.scalars(select(RawSignal).where(RawSignal.validity_state == "active"))
    )
    for signal in signals:
        if expire_signal_if_due(signal, now_utc=now_utc):
            expired_signals += 1
            refreshed_events += refresh_signal_events(session, signal, now_utc=now_utc)
    expired_alerts = expire_alerts(session, now_utc=now_utc)
    return LifecycleRefreshResult(expired_signals, expired_alerts, refreshed_events)
