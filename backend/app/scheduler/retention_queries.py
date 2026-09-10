"""保留清理的只读引用事实查询：提醒锚点分类与受保护信号集合。

本模块只读引用事实，绝不修改提醒、研究记录或名单成员状态：

- 尚保留提醒 = current 有效提醒（复用 ``current_alert_condition``），
  加上锚点仍在保留窗口内或缺失锚点的 expired 提醒（legacy 一并保守保留）。
- 受保护信号 = 尚保留提醒闭包（match → event → RiskEventSignal）连接的信号
  ∪ ``research_claims.promoted_signal_id`` 溯源引用的信号
  ∪ 活跃名单成员资格事实（同 source/member_key 且 status=active）引用的信号。

expired 提醒删除锚点优先 ``expires_at``；无 ``expires_at`` 的无界撤销记录使用
已验证存在的终止更新 ``updated_at`` 作为保守锚点（``updated_at != created_at``
才算发生终止更新）；异常缺失锚点不删并计数。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import DateTime, and_, bindparam, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.research.models import ResearchClaim
from app.risks.models import RiskAlert, RiskEventSignal, SupplierEventMatch
from app.risks.query_validity import current_alert_condition
from app.signals.models import RawSignal, SourceMemberState


@dataclass(frozen=True, slots=True)
class ExpiredAlertClassification:
    """expired 提醒按删除锚点分类；缺锚者保守保留并单独计数。"""

    deletable: tuple[RiskAlert, ...]
    anomaly_anchor_count: int


def alert_removal_anchor(alert: RiskAlert) -> datetime | None:
    """expired 提醒删除锚点：优先 expires_at；无界撤销记录要求已验证的终止更新。

    expires_at 缺失时以 updated_at 作为保守锚点，但必须验证实际终止更新存在
    （updated_at 与 created_at 不同）；从未发生终止更新的记录视为异常缺锚，
    保守保留并计数（schema 层 updated_at NOT NULL，缺 NULL 仅防御分支）。
    """
    if alert.expires_at is not None:
        return alert.expires_at
    if alert.updated_at is not None and alert.updated_at != alert.created_at:
        return alert.updated_at
    return None


def classify_expired_alerts(
    session: Session, *, now_utc: datetime, event_days: int
) -> ExpiredAlertClassification:
    """按固定 id 顺序锁定全部 expired 提醒并分类（严格 ``< cutoff`` 删除边界）。"""
    cutoff = now_utc - timedelta(days=event_days)
    rows = list(
        session.scalars(
            select(RiskAlert)
            .where(RiskAlert.status == "expired")
            .order_by(RiskAlert.id)
            .with_for_update()
        )
    )
    deletable: list[RiskAlert] = []
    anomaly_anchor_count = 0
    for alert in rows:
        anchor = alert_removal_anchor(alert)
        if anchor is None:
            anomaly_anchor_count += 1
        elif anchor < cutoff:
            deletable.append(alert)
    return ExpiredAlertClassification(
        deletable=tuple(deletable), anomaly_anchor_count=anomaly_anchor_count
    )


def retained_alert_condition(
    *, now_utc: datetime, event_days: int
) -> ColumnElement[bool]:
    """尚保留提醒条件：current 有效提醒谓词 + 窗口内/异常锚点的 expired 提醒。"""
    cutoff_parameter = bindparam(
        "retention_alert_cutoff",
        value=now_utc - timedelta(days=event_days),
        type_=DateTime(timezone=True),
    )
    termination_anchor = func.coalesce(RiskAlert.expires_at, RiskAlert.updated_at)
    unverified_termination = and_(
        RiskAlert.expires_at.is_(None),
        RiskAlert.updated_at == RiskAlert.created_at,
    )
    return or_(
        current_alert_condition(now_utc),
        and_(
            RiskAlert.status == "expired",
            or_(
                termination_anchor.is_(None),
                termination_anchor >= cutoff_parameter,
                unverified_termination,
            ),
        ),
    )


def protected_signal_ids(
    session: Session, *, now_utc: datetime, event_days: int
) -> frozenset[int]:
    """计算本轮受保护信号集合：提醒闭包 ∪ 研究溯源 ∪ 活跃成员资格事实。"""
    retained_event_ids = (
        select(SupplierEventMatch.event_id)
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .where(retained_alert_condition(now_utc=now_utc, event_days=event_days))
    )
    closure_ids = session.scalars(
        select(RiskEventSignal.signal_id).where(
            RiskEventSignal.event_id.in_(retained_event_ids)
        )
    )
    promoted_rows = session.scalars(
        select(ResearchClaim.promoted_signal_id).where(
            ResearchClaim.promoted_signal_id.is_not(None)
        )
    )
    promoted_ids = frozenset(pid for pid in promoted_rows if pid is not None)
    membership_ids = session.scalars(
        select(RawSignal.id).join(
            SourceMemberState,
            and_(
                SourceMemberState.source_id == RawSignal.source_id,
                SourceMemberState.member_key == RawSignal.external_id,
                SourceMemberState.status == "active",
            ),
        )
    )
    return (
        frozenset(closure_ids)
        | promoted_ids
        | frozenset(membership_ids)
    )
