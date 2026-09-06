"""风险提醒的规则版本切割与持久化。"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.risks.models import RiskAlert, SupplierEventMatch


@dataclass(frozen=True, slots=True)
class AlertValues:
    level: str
    score: int
    score_detail: dict[str, object]
    expires_at: datetime | None
    now_utc: datetime


def upsert_alert(
    session: Session, match: SupplierEventMatch, values: AlertValues
) -> RiskAlert:
    """保存新策略提醒；legacy 提醒保持迁移前快照，不重算或恢复。"""
    alert = session.scalar(
        select(RiskAlert).where(
            RiskAlert.match_id == match.id,
            RiskAlert.status == "current",
        )
    )
    if alert is not None and alert.expiry_kind == "legacy":
        return alert
    if (
        alert is not None
        and alert.score_detail.get("rule_version")
        != values.score_detail["rule_version"]
    ):
        alert.status = "expired"
        alert.updated_at = values.now_utc
        session.flush()
        alert = None
    if alert is None:
        alert = session.scalar(
            select(RiskAlert)
            .where(
                RiskAlert.match_id == match.id,
                RiskAlert.status == "expired",
                RiskAlert.expiry_kind != "legacy",
                RiskAlert.score_detail["rule_version"].astext
                == str(values.score_detail["rule_version"]),
            )
            .order_by(RiskAlert.updated_at.desc(), RiskAlert.id.desc())
        )
    expiry_kind = "unbounded" if values.expires_at is None else "finite"
    if alert is None:
        alert = RiskAlert(
            match_id=match.id,
            level=values.level,
            score=values.score,
            score_detail=values.score_detail,
            status="current",
            expires_at=values.expires_at,
            expiry_kind=expiry_kind,
        )
        session.add(alert)
        session.flush()
        return alert
    alert.level = values.level
    alert.score = values.score
    alert.score_detail = values.score_detail
    alert.expires_at = values.expires_at
    alert.expiry_kind = expiry_kind
    alert.status = "current"
    alert.updated_at = values.now_utc
    return alert
