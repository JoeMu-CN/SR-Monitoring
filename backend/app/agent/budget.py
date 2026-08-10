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


def _source_has_key(source: DataSource | None) -> bool:
    """天眼查是否有可用运行密钥：控制台密文优先，非生产环境允许兼容回退。"""
    if source is not None and source.api_key_encrypted:
        from app.signals.secret_store import decrypt_secret

        if decrypt_secret(source.api_key_encrypted):
            return True
    return bool(config.get_tyc_env_fallback())


def _limit_value(value: object, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value) if isinstance(value, (int, str)) else default
    except (TypeError, ValueError):
        return default
    return parsed if 1 <= parsed <= maximum else default


def _source_limits(source: DataSource | None) -> tuple[int, int]:
    daily_default, monthly_default = config.get_tyc_budget_fallback()
    values = (
        source.login_config
        if source is not None and isinstance(source.login_config, dict)
        else {}
    )
    return (
        _limit_value(values.get("daily_limit"), daily_default, 1000),
        _limit_value(values.get("monthly_limit"), monthly_default, 10000),
    )


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
    source = session.scalar(select(DataSource).where(DataSource.code == TYC_SOURCE_CODE))
    daily_limit, monthly_limit = _source_limits(source)
    return TycUsageSnapshot(
        enabled=bool(source is not None and source.enabled and _source_has_key(source)),
        daily_used=daily_used,
        daily_limit=daily_limit,
        monthly_used=monthly_used,
        monthly_limit=monthly_limit,
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
