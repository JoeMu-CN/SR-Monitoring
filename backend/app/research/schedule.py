"""周期研究调度配置、额度预检与运行时开关。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    RESEARCH_BOCHA_MANUAL_RESERVE,
    RESEARCH_BOCHA_MONTHLY_BATCH_LIMIT,
    RESEARCH_BOCHA_SAFETY_RESERVE,
    RESEARCH_MONTHLY_SUPPLIER_LIMIT,
    RESEARCH_MONTHLY_SUPPLIER_SCOPE,
    RESEARCH_TRACK_ENABLED,
    get_search_settings,
)
from app.research.models import ResearchProviderQuotaPeriod, ResearchScheduleConfig
from app.suppliers.models import Supplier

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_WEEKLY_CRON = "30 8 * * mon"
DEFAULT_WEEKLY_BUDGET: dict[str, object] = {
    "max_queries": 2,
    "max_results": 40,
    "max_pages": 20,
    "max_input_tokens": 20_000,
    "max_output_tokens": 5_000,
}


def select_monthly_suppliers(session: Session) -> list[Supplier]:
    """按月报范围配置稳定选择供应商；不修改供应商主数据。"""
    query = select(Supplier).where(Supplier.enabled.is_(True)).order_by(Supplier.id)
    if RESEARCH_MONTHLY_SUPPLIER_SCOPE == "limited":
        query = query.limit(RESEARCH_MONTHLY_SUPPLIER_LIMIT)
    return list(session.scalars(query))


class ResearchScheduleConfigurationError(ValueError):
    """周期研究配置不满足格式或安全边界。"""


class ResearchSchedulePreflightBlocked(RuntimeError):
    """周报启用前的额度/容量预检未通过。"""

    def __init__(self, preflight: WeeklySchedulePreflight) -> None:
        super().__init__(preflight.block_reason or "周报启用预检未通过")
        self.preflight = preflight


@dataclass(frozen=True)
class WeeklySchedulePreflight:
    provider: str
    period_key: str
    enabled_supplier_count: int
    monthly_trigger_count: int
    max_searches_per_supplier: int
    estimated_monthly_searches: int
    reserved_searches: int
    required_monthly_searches: int
    provider_monthly_limit: int
    provider_used: int
    provider_reserved: int
    provider_remaining: int
    approved_monthly_quota: int | None
    can_enable: bool
    block_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "period_key": self.period_key,
            "enabled_supplier_count": self.enabled_supplier_count,
            "monthly_trigger_count": self.monthly_trigger_count,
            "max_searches_per_supplier": self.max_searches_per_supplier,
            "estimated_monthly_searches": self.estimated_monthly_searches,
            "reserved_searches": self.reserved_searches,
            "required_monthly_searches": self.required_monthly_searches,
            "provider_monthly_limit": self.provider_monthly_limit,
            "provider_used": self.provider_used,
            "provider_reserved": self.provider_reserved,
            "provider_remaining": self.provider_remaining,
            "approved_monthly_quota": self.approved_monthly_quota,
            "can_enable": self.can_enable,
            "block_reason": self.block_reason,
        }


def _period_key(now: datetime) -> str:
    return now.astimezone(SHANGHAI_TZ).strftime("%Y-%m")


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    current = now.astimezone(SHANGHAI_TZ)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def count_monthly_cron_triggers(cron_expression: str, *, now: datetime) -> int:
    """计算当前自然月 cron 的实际触发点数量，不依赖 Scheduler 运行状态。"""
    normalized = cron_expression.strip()
    if not normalized:
        raise ResearchScheduleConfigurationError("周期研究 cron 不能为空")
    try:
        trigger = CronTrigger.from_crontab(normalized, timezone=SHANGHAI_TZ)
    except (TypeError, ValueError) as exc:
        raise ResearchScheduleConfigurationError("周期研究 cron 表达式无效") from exc
    start, end = _month_bounds(now)
    cursor = start - timedelta(seconds=1)
    count = 0
    while True:
        next_fire = trigger.get_next_fire_time(cursor, cursor)
        if next_fire is None or next_fire >= end:
            break
        if next_fire >= start:
            count += 1
        cursor = next_fire
        if count > 1000:
            raise ResearchScheduleConfigurationError("周期研究 cron 触发频率超过安全上限")
    return count


def get_schedule_config(
    session: Session, *, schedule_type: str = "weekly"
) -> ResearchScheduleConfig | None:
    if schedule_type not in {"weekly", "monthly"}:
        raise ValueError("不支持的周期研究配置类型")
    return session.scalar(
        select(ResearchScheduleConfig).where(
            ResearchScheduleConfig.schedule_type == schedule_type
        )
    )


def _budget_max_queries(budget_template: dict[str, object]) -> int:
    raw = budget_template.get("max_queries", DEFAULT_WEEKLY_BUDGET["max_queries"])
    if not isinstance(raw, int) or isinstance(raw, bool) or not 1 <= raw <= 20:
        raise ResearchScheduleConfigurationError("周期研究预算模板 max_queries 必须为 1-20 的整数")
    return raw


def _schedule_preflight(
    session: Session,
    *,
    cron_expression: str,
    topic_template: str,
    budget_template: dict[str, object],
    approved_monthly_quota: int | None,
    require_approval: bool,
    reserve_monthly_batch: bool,
    monthly_batch_limit: int | None = None,
    supplier_count: int | None = None,
    schedule_label: str = "周期研究",
    now: datetime | None = None,
) -> WeeklySchedulePreflight:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("周报预检时间必须带时区")
    trigger_count = count_monthly_cron_triggers(cron_expression, now=current)
    max_queries = _budget_max_queries(budget_template)
    if supplier_count is None:
        if schedule_label == "月报":
            supplier_count = len(select_monthly_suppliers(session))
        else:
            supplier_count = session.scalar(
                select(func.count()).select_from(Supplier).where(Supplier.enabled.is_(True))
            ) or 0
    settings = get_search_settings()
    provider = settings.provider.strip().lower()
    period_key = _period_key(current)
    quota = session.scalar(
        select(ResearchProviderQuotaPeriod).where(
            ResearchProviderQuotaPeriod.provider == provider,
            ResearchProviderQuotaPeriod.period_key == period_key,
        )
    ) if provider not in {"", "none", "fake"} else None
    provider_limit = quota.monthly_limit if quota is not None else settings.monthly_limit
    provider_used = quota.used if quota is not None else 0
    provider_reserved = (
        (quota.scheduled_reserved + quota.manual_reserved) if quota is not None else 0
    )
    provider_remaining = max(provider_limit - provider_used - provider_reserved, 0)
    estimated = supplier_count * trigger_count * max_queries
    reserved = (
        (RESEARCH_BOCHA_MONTHLY_BATCH_LIMIT if reserve_monthly_batch else 0)
        + RESEARCH_BOCHA_MANUAL_RESERVE
        + RESEARCH_BOCHA_SAFETY_RESERVE
    )
    required = estimated + reserved
    reasons: list[str] = []
    if not RESEARCH_TRACK_ENABLED:
        reasons.append("研究轨总开关已关闭")
    if not topic_template.strip():
        reasons.append(f"{schedule_label}主题未配置")
    if supplier_count < 1:
        reasons.append("没有启用供应商")
    if trigger_count < 1:
        reasons.append(f"当前自然月没有可触发的{schedule_label}时间点")
    if provider in {"", "none", "fake"} or not settings.api_key:
        reasons.append("搜索 Provider 未配置")
    if provider_remaining < required:
        reasons.append(
            f"Provider 月度可用额度不足：需要 {required}，剩余 {provider_remaining}"
        )
    if monthly_batch_limit is not None and estimated > monthly_batch_limit:
        reasons.append(
            f"{schedule_label}预计搜索量超过上限：需要 {estimated}，上限 {monthly_batch_limit}"
        )
    if require_approval and (
        approved_monthly_quota is None or approved_monthly_quota < required
    ):
        reasons.append(f"批准额度不足：需要至少 {required}")
    return WeeklySchedulePreflight(
        provider=provider or "none",
        period_key=period_key,
        enabled_supplier_count=supplier_count,
        monthly_trigger_count=trigger_count,
        max_searches_per_supplier=max_queries,
        estimated_monthly_searches=estimated,
        reserved_searches=reserved,
        required_monthly_searches=required,
        provider_monthly_limit=provider_limit,
        provider_used=provider_used,
        provider_reserved=provider_reserved,
        provider_remaining=provider_remaining,
        approved_monthly_quota=approved_monthly_quota,
        can_enable=not reasons,
        block_reason="；".join(reasons) if reasons else None,
    )


def weekly_schedule_preflight(
    session: Session,
    *,
    cron_expression: str,
    topic_template: str,
    budget_template: dict[str, object],
    approved_monthly_quota: int | None,
    now: datetime | None = None,
) -> WeeklySchedulePreflight:
    return _schedule_preflight(
        session,
        cron_expression=cron_expression,
        topic_template=topic_template,
        budget_template=budget_template,
        approved_monthly_quota=approved_monthly_quota,
        require_approval=True,
        reserve_monthly_batch=True,
        schedule_label="周报",
        now=now,
    )


def monthly_schedule_preflight(
    session: Session,
    *,
    cron_expression: str,
    topic_template: str,
    budget_template: dict[str, object],
    supplier_count: int | None = None,
    now: datetime | None = None,
) -> WeeklySchedulePreflight:
    return _schedule_preflight(
        session,
        cron_expression=cron_expression,
        topic_template=topic_template,
        budget_template=budget_template,
        approved_monthly_quota=None,
        require_approval=False,
        reserve_monthly_batch=False,
        monthly_batch_limit=RESEARCH_BOCHA_MONTHLY_BATCH_LIMIT,
        schedule_label="月报",
        supplier_count=supplier_count,
        now=now,
    )


def save_weekly_schedule_config(
    session: Session,
    *,
    updated_by_user_id: int,
    enabled: bool,
    cron_expression: str,
    topic_template: str,
    budget_template: dict[str, object],
    approved_monthly_quota: int | None,
    approval_note: str | None,
    now: datetime | None = None,
) -> tuple[ResearchScheduleConfig, WeeklySchedulePreflight]:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("周报配置时间必须带时区")
    normalized_cron = cron_expression.strip()
    normalized_topic = topic_template.strip()
    if not normalized_cron:
        raise ResearchScheduleConfigurationError("周报 cron 不能为空")
    _ = count_monthly_cron_triggers(normalized_cron, now=current)
    normalized_budget = dict(budget_template or DEFAULT_WEEKLY_BUDGET)
    _budget_max_queries(normalized_budget)
    existing = get_schedule_config(session, schedule_type="weekly")
    if existing is not None:
        if not normalized_topic:
            normalized_topic = existing.topic_template
        if not budget_template:
            normalized_budget = dict(existing.budget_template)
        if approved_monthly_quota is None:
            approved_monthly_quota = existing.approved_monthly_quota
        if approval_note is None:
            approval_note = existing.approval_note
    if enabled:
        if not (approval_note or "").strip():
            raise ResearchScheduleConfigurationError("启用周报必须填写批准说明")
        preflight = weekly_schedule_preflight(
            session,
            cron_expression=normalized_cron,
            topic_template=normalized_topic,
            budget_template=normalized_budget,
            approved_monthly_quota=approved_monthly_quota,
            now=current,
        )
        if not preflight.can_enable:
            raise ResearchSchedulePreflightBlocked(preflight)
    else:
        preflight = weekly_schedule_preflight(
            session,
            cron_expression=normalized_cron,
            topic_template=normalized_topic,
            budget_template=normalized_budget,
            approved_monthly_quota=approved_monthly_quota,
            now=current,
        )
    config = existing or ResearchScheduleConfig(schedule_type="weekly")
    config.enabled = enabled
    config.cron_expression = normalized_cron
    config.topic_template = normalized_topic
    config.budget_template = normalized_budget
    config.approved_monthly_quota = approved_monthly_quota
    config.approval_note = (approval_note or "").strip() or None
    config.updated_by_user_id = updated_by_user_id
    if enabled:
        config.approved_by_user_id = updated_by_user_id
        config.approved_at = current
    session.add(config)
    session.flush()
    return config, preflight
