"""天眼查调用预算控制器。

负责：额度检查、调用记账、余量查询。
计费口径（Todo 1 实测）：只有「有记录的成功结果」计 1 次（等价于 Todo 1 的
``success_with_records``）；empty（无记录）、error、param_missing 与
not_configured 均计 0 次。日/月窗口统一按北京时间（UTC+8）计算。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.models import TycUsageRecord
from app.signals.models import DataSource

BEIJING_OFFSET = timedelta(hours=8)
TYC_SOURCE_CODE = "tianyancha"
DEFAULT_DAILY_LIMIT = 1000
DEFAULT_MONTHLY_LIMIT = 10000

# 写入 TycUsageRecord.status 的合法四态；其余状态值拒绝记账。
VALID_USAGE_STATUSES: frozenset[str] = frozenset(
    {"success", "empty", "error", "not_configured"}
)
# 计费状态集合：只有 success（= 有记录的成功结果，Todo 1 的 success_with_records）
# 计入额度消耗；空结果与错误高估消耗，一律不计费。
CHARGED_STATUSES: frozenset[str] = frozenset({"success"})


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
    """天眼查是否有可用运行密钥：仅认信息源控制台可解密的密文。"""
    if source is not None and source.api_key_encrypted:
        from app.signals.secret_store import decrypt_secret

        if decrypt_secret(source.api_key_encrypted):
            return True
    return False


def _limit_value(value: object, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value) if isinstance(value, (int, str)) else default
    except (TypeError, ValueError):
        return default
    return parsed if 1 <= parsed <= maximum else default


def _source_limits(source: DataSource | None) -> tuple[int, int]:
    values = (
        source.login_config
        if source is not None and isinstance(source.login_config, dict)
        else {}
    )
    return (
        _limit_value(values.get("daily_limit"), DEFAULT_DAILY_LIMIT, 100000),
        _limit_value(values.get("monthly_limit"), DEFAULT_MONTHLY_LIMIT, 1000000),
    )


def get_tyc_usage(session: Session) -> TycUsageSnapshot:
    """按北京时间日/月窗口统计已消耗额度。"""
    beijing_now = datetime.now(UTC) + BEIJING_OFFSET
    day_start_utc = (
        beijing_now.replace(hour=0, minute=0, second=0, microsecond=0)
        - BEIJING_OFFSET
    )
    month_start_utc = (
        beijing_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        - BEIJING_OFFSET
    )

    daily_used = _count_charged(session, since=day_start_utc)
    monthly_used = _count_charged(session, since=month_start_utc)
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
    """记录一次调用结果。只有 success（有记录成功）计入额度消耗。"""
    if status not in VALID_USAGE_STATUSES:
        raise ValueError(f"非法状态：{status}")
    session.add(
        TycUsageRecord(
            tool_name=tool_name,
            company_name=company_name,
            status=status,
        )
    )


def _count_charged(session: Session, *, since: datetime) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TycUsageRecord)
            .where(
                TycUsageRecord.status.in_(CHARGED_STATUSES),
                TycUsageRecord.called_at >= since,
            )
        )
        or 0
    )
