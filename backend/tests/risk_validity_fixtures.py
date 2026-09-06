"""任务 8 风险有效期数据库测试夹具。"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.risks.models import RiskAlert, RiskEvent, RiskEventSignal, SupplierEventMatch
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier


@dataclass(frozen=True, slots=True)
class SignalSpec:
    name: str
    state: str = "active"
    valid_until: datetime | None = None
    review_due_at: datetime | None = None
    mode: str = "fixed_days"
    profile: str = "weather_alert"
    validity_key: str | None = None


@dataclass(frozen=True, slots=True)
class LinkedRisk:
    source: DataSource
    event: RiskEvent
    alert: RiskAlert
    signals: tuple[RawSignal, ...]


def linked_risk(
    session: Session, specs: tuple[SignalSpec, ...], *, now_utc: datetime
) -> LinkedRisk:
    """创建已关联的信号、事件、供应商匹配和 current 提醒。"""
    source = DataSource(
        code=f"task-8-{specs[0].name}",
        name="任务 8 测试源",
        source_type="api",
        credibility=90,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    session.add(source)
    session.flush()
    signals = tuple(
        RawSignal(
            source_id=source.id,
            external_id=spec.name,
            title=spec.name,
            content=spec.name,
            published_at=now_utc,
            collected_at=now_utc,
            fingerprint=f"task-8-{spec.name}",
            raw_data={},
            validity_profile=(
                spec.profile if spec.state != "pending_classification" else None
            ),
            validity_state=spec.state,
            valid_from=now_utc,
            valid_until=spec.valid_until,
            review_due_at=spec.review_due_at,
            validity_mode=(
                spec.mode if spec.state != "pending_classification" else None
            ),
            validity_key=spec.validity_key,
            lifecycle_action="assert",
            validity_policy_version=(
                f"policy-{spec.name}"
                if spec.state != "pending_classification"
                else None
            ),
            validity_reason={
                "code": (
                    "policy_resolved"
                    if spec.state != "pending_classification"
                    else "pending_classification"
                ),
                "anchor_source": "published_at",
                "details": {},
            },
        )
        for spec in specs
    )
    session.add_all(signals)
    session.flush()
    event = RiskEvent(
        dedup_key=f"task-8-{specs[0].name}",
        event_type="weather",
        severity="high",
        summary="任务 8 测试事件",
        confidence=0.9,
        facts={},
        validity_state="active",
        validity_reason={
            "code": "effective_signal_support",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(event)
    session.flush()
    session.add_all(
        RiskEventSignal(event_id=event.id, signal_id=signal.id) for signal in signals
    )
    supplier = Supplier(
        supplier_code=f"TASK-8-{specs[0].name}",
        legal_name="任务 8 供应商",
        country_code="CN",
    )
    session.add(supplier)
    session.flush()
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="legal_name",
        score=30,
        reasons=["测试"],
        evidence=[],
    )
    session.add(match)
    session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level="P3",
        score=60,
        score_detail={"rule_version": "rule-v1"},
        status="current",
        expires_at=None,
        expiry_kind="unbounded",
    )
    session.add(alert)
    session.flush()
    return LinkedRisk(source, event, alert, signals)
