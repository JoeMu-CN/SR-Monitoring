"""风险信号、事件与提醒的 Scheduler 生命周期协调。"""

import logging
from datetime import UTC, datetime

from app.database import SessionLocal
from app.risks.validity import coordinate_risk_lifecycle

logger = logging.getLogger("scheduler")


def risk_validity_job(*, now_utc: datetime | None = None) -> None:
    """以单次 UTC 时钟在同一事务物化到期信号、事件和提醒。"""
    now = now_utc or datetime.now(UTC)
    with SessionLocal() as session:
        try:
            result = coordinate_risk_lifecycle(session, now_utc=now)
            session.commit()
            if result.expired_signals or result.expired_alerts:
                logger.info(
                    "风险有效期物化完成: signals=%d events=%d alerts=%d",
                    result.expired_signals,
                    result.refreshed_events,
                    result.expired_alerts,
                )
        # Scheduler 顶层边界必须记录异常并回滚整批事务。
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("风险有效期物化任务异常: %s", exc)
