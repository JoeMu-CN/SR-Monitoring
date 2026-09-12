"""调度与健康口径的纯规则：5 段 cron 解析、单源状态分类、overall 真值表。

本模块不做 IO、不访问数据库，时间一律 UTC datetime。职责集中于此的原因：
- scheduler 注册表与只读健康聚合共用同一份 cron 解析（单一事实来源，避免漂移）；
- health.py 只做数据聚合，全部状态判定在此，便于对 300 秒 overdue 宽限与
  四态 overall 真值表做无副作用的边界测试。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from app.scheduler.health_schemas import (
    HeartbeatStatus,
    OverallStatus,
    RunStatus,
    SourceHealthStatus,
)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
# 单源 cron 触发点晚于该宽限仍未见成功即判 overdue（覆盖进程重启与轻微漂移）。
OVERDUE_GRACE_SECONDS = 300
# 参与独立 cron 调度的适配器状态；其余（unconfigured/draft/invalid）不算可调度。
ACTIVE_ADAPTER_STATES = ("builtin", "published")

# 稳定脱敏 reason_code 词表：与 state 一一对应，ok 态说明判定依据。
ReasonCode = Literal[
    "success_observed",
    "last_attempt_failed",
    "missed_expected_trigger",
    "never_run",
    "disabled",
    "on_demand",
    "invalid_schedule",
]


def cron_to_apscheduler(expr: str) -> dict[str, str]:
    """将 '分 时 日 月 周' 5 段 cron 拆成 APScheduler CronTrigger 参数。"""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"非法 cron 表达式: {expr}")
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


def next_fire_after(schedule: str, anchor: datetime) -> datetime | None:
    """以 5 段 cron（Asia/Shanghai）构造触发器，返回锚点之后的下一触发点。"""
    trigger = CronTrigger(timezone=SHANGHAI_TZ, **cron_to_apscheduler(schedule))
    fire = trigger.get_next_fire_time(None, anchor.astimezone(SHANGHAI_TZ))
    return None if fire is None else fire.astimezone(UTC)


def classify_schedule_source(
    *,
    schedule: str,
    anchor: datetime | None,
    latest_success_at: datetime | None,
    latest_failure_at: datetime | None,
    now: datetime,
) -> tuple[SourceHealthStatus, ReasonCode, datetime | None]:
    """按计划口径分类单个可调度拉取源，返回 (state, reason_code, next_expected_at)。

    - cron 非法 → invalid_schedule（先于一切，避免把坏调度误报成未运行）；
    - 最新一次失败且未被更新的成功恢复 → failed；
    - 无成功观测：以首次创建/最近配置更新锚点推导首个预期触发点，
      越过 300 秒宽限 → overdue，否则 never_run；
    - 有成功观测：从最近成功锚点推导下一触发点，越过 300 秒宽限 → overdue，
      否则 ok（成功零新增同样是 ok）。
    """
    try:
        if anchor is None:
            # 无锚点也要先确认 cron 合法，避免非法调度被误报为 never_run。
            next_fire_after(schedule, now)
            expected = None
        else:
            expected = next_fire_after(schedule, anchor)
    except (ValueError, TypeError):
        return ("invalid_schedule", "invalid_schedule", None)
    if latest_failure_at is not None and (
        latest_success_at is None or latest_failure_at >= latest_success_at
    ):
        return ("failed", "last_attempt_failed", expected)
    if latest_success_at is None:
        if (
            expected is not None
            and now > expected + timedelta(seconds=OVERDUE_GRACE_SECONDS)
        ):
            return ("overdue", "missed_expected_trigger", expected)
        return ("never_run", "never_run", expected)
    if (
        expected is not None
        and now > expected + timedelta(seconds=OVERDUE_GRACE_SECONDS)
    ):
        return ("overdue", "missed_expected_trigger", expected)
    return ("ok", "success_observed", expected)


def overall_status(
    *,
    scheduler_status: HeartbeatStatus,
    processing_total: int,
    oldest_pending_age_seconds: int | None,
    last_run_status: RunStatus,
    source_states: Iterable[SourceHealthStatus],
) -> OverallStatus:
    """overall 真值表。

    1. 所有来源 disabled/on_demand 且无待处理 → inactive；
    2. 还有需要调度/待处理的工作但从未有心跳 → unknown（无运行证据不得宣称正常）；
    3. 心跳过期、来源 failed/overdue、积压 oldest>3600 秒、上一轮处理失败 → degraded；
    4. 其余确有新鲜观测 → ok。
    """
    states = set(source_states)
    schedulable = bool(states - {"disabled", "on_demand"})
    if not schedulable and processing_total == 0:
        return "inactive"
    if scheduler_status == "unknown":
        return "unknown"
    if scheduler_status == "stale":
        return "degraded"
    if states & {"failed", "overdue"}:
        return "degraded"
    if processing_total > 0 and (oldest_pending_age_seconds or 0) > 3600:
        return "degraded"
    if last_run_status == "failed":
        return "degraded"
    return "ok"
