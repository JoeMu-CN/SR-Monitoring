"""风险信号与提醒的实时 SQL 判活谓词。"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import DateTime, and_, bindparam, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.risks.models import RiskAlert
from app.signals.models import DataSource, RawSignal
from app.signals.validity import ProcessingState, ValidityMode, ValidityState

FINITE_SIGNAL_MODES: Final = (
    ValidityMode.FIXED_DAYS,
    ValidityMode.UNTIL_SUPERSEDED,
    ValidityMode.EVENT_END_PLUS_GRACE,
)
UNBOUNDED_SIGNAL_MODES: Final = (
    ValidityMode.UNTIL_REVOKED,
    ValidityMode.INDEFINITE,
)

# 可作为有效证据的处理状态：尚未分析但已确认生命周期有效，以及已完成分析。
# filtered / no_trigger / classification_failed 一律不计有效证据。
VALID_EVIDENCE_PROCESSING_STATES: Final = (
    ProcessingState.PENDING_CLASSIFICATION,
    ProcessingState.ANALYZED,
)


def current_alert_condition(now_utc: datetime) -> ColumnElement[bool]:
    """返回提醒实时判活条件；legacy 继续只服从旧 status 真值。"""
    now_parameter = bindparam(
        "now_utc",
        value=now_utc,
        type_=DateTime(timezone=True),
    )
    return and_(
        RiskAlert.status == "current",
        or_(
            and_(
                RiskAlert.expiry_kind == "finite",
                RiskAlert.expires_at > now_parameter,
            ),
            and_(
                RiskAlert.expiry_kind == "unbounded",
                RiskAlert.expires_at.is_(None),
            ),
            RiskAlert.expiry_kind == "legacy",
        ),
    )


def live_alert_ids(
    session: Session,
    *,
    now_utc: datetime,
    alert_ids: Sequence[int] | None = None,
) -> set[int]:
    """返回满足 ``current_alert_condition`` 的提醒 ID 集合。

    事件簇、事件详情与组级状态都用它做逐行判活：判活规则只留在 SQL 谓词里，
    Python 侧只做集合查找，避免出现「只认 status 字段」的第二套口径。
    """
    statement = select(RiskAlert.id).where(current_alert_condition(now_utc))
    if alert_ids is not None:
        statement = statement.where(RiskAlert.id.in_(list(alert_ids)))
    return set(session.scalars(statement))


def valid_signal_condition(
    source: DataSource, *, now_utc: datetime
) -> ColumnElement[bool]:
    """返回信号实时判活条件；legacy 保留旧信源天数口径。

    有效证据只认「生命周期 active」且「处理状态仍可支撑风险结论」：
    pending_classification / analyzed 计入，filtered / no_trigger /
    classification_failed 不计入。legacy 分支保持旧口径不变。
    """
    now_parameter = bindparam(
        "now_utc",
        value=now_utc,
        type_=DateTime(timezone=True),
    )
    active_condition = and_(
        RawSignal.validity_state == ValidityState.ACTIVE,
        RawSignal.processing_state.in_(VALID_EVIDENCE_PROCESSING_STATES),
        or_(
            and_(
                RawSignal.validity_mode.in_(FINITE_SIGNAL_MODES),
                RawSignal.valid_until > now_parameter,
            ),
            and_(
                RawSignal.validity_mode.in_(UNBOUNDED_SIGNAL_MODES),
                or_(
                    RawSignal.valid_until.is_(None),
                    RawSignal.valid_until > now_parameter,
                ),
            ),
        ),
        or_(
            RawSignal.review_due_at.is_(None),
            RawSignal.review_due_at > now_parameter,
        ),
    )
    legacy_condition: ColumnElement[bool] = (
        RawSignal.validity_state == ValidityState.LEGACY
    )
    if source.signal_validity_days is not None:
        cutoff_parameter = bindparam(
            "legacy_signal_cutoff",
            value=now_utc - timedelta(days=source.signal_validity_days),
            type_=DateTime(timezone=True),
        )
        legacy_condition = and_(
            legacy_condition,
            or_(
                RawSignal.published_at >= cutoff_parameter,
                and_(
                    RawSignal.published_at.is_(None),
                    RawSignal.collected_at >= cutoff_parameter,
                ),
            ),
        )
    return or_(active_condition, legacy_condition)
