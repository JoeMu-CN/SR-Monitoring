"""天眼查调用预算控制器。

负责：额度检查、调用记账、余量查询。
计费口径：只有 status == "success" 的记录计入消耗（与天眼查 AI 平台规则一致：
成功且返回有效结果计 1 次；报错、全部无结果、系统异常和未配置计 0 次）。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.agent.models import TycUsageRecord
from app.config import AGENT_TYC_DAILY_LIMIT, AGENT_TYC_MONTHLY_LIMIT
from app.signals.models import DataSource

BEIJING_OFFSET = timedelta(hours=8)
TYC_SOURCE_CODE = "tianyancha"


@dataclass(frozen=True)
class TycUsageSnapshot:
    enabled: bool
    daily_used: int
    daily_limit: int
    monthly_used: int
    monthly_limit: int

    @property
    def daily_remaining(self) -> int:
        return max(self.daily_limit - self.daily_used, 0)

    @property
    def monthly_remaining(self) -> int:
        return max(self.monthly_limit - self.monthly_used, 0)

    @property
    def allowed(self) -> bool:
        return self.daily_remaining > 0 and self.monthly_remaining > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "daily_used": self.daily_used,
            "daily_limit": self.daily_limit,
            "daily_remaining": self.daily_remaining,
            "monthly_used": self.monthly_used,
            "monthly_limit": self.monthly_limit,
            "monthly_remaining": self.monthly_remaining,
        }


def get_tyc_usage(session: Session) -> TycUsageSnapshot:
    now_utc = datetime.now(UTC)
    beijing_today = now_utc + BEIJING_OFFSET
    day_start_utc = (
        beijing_today.replace(hour=0, minute=0, second=0, microsecond=0)
        - BEIJING_OFFSET
    )
    month_start_utc = datetime(now_utc.year, now_utc.month, 1, tzinfo=UTC)

    daily_used = _count_success(session, since=day_start_utc)
    monthly_used = _count_success(session, since=month_start_utc)
    source_enabled = session.scalar(
        select(DataSource.enabled).where(DataSource.code == TYC_SOURCE_CODE)
    )
    return TycUsageSnapshot(
        enabled=bool(source_enabled and config.TYC_API_KEY),
        daily_used=daily_used,
        daily_limit=AGENT_TYC_DAILY_LIMIT,
        monthly_used=monthly_used,
        monthly_limit=AGENT_TYC_MONTHLY_LIMIT,
    )


def record_tyc_usage(
    session: Session,
    *,
    tool_name: str,
    company_name: str,
    status: str,
) -> None:
    """记录一次调用结果。只有 success 会计入额度消耗。"""
    if status not in {"success", "empty", "error", "not_configured"}:
        raise ValueError(f"非法状态：{status}")
    session.add(
        TycUsageRecord(
            tool_name=tool_name,
            company_name=company_name,
            status=status,
        )
    )


def _count_success(session: Session, *, since: datetime) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TycUsageRecord)
            .where(
                TycUsageRecord.status == "success",
                TycUsageRecord.called_at >= since,
            )
        )
        or 0
    )
