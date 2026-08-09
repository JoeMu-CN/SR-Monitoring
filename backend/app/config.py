import os
from dataclasses import dataclass

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://supplier_risk:local_mvp_change_me@postgres:5432/supplier_risk",
)


@dataclass(frozen=True)
class AISettings:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    max_retries: int


def get_ai_settings() -> AISettings:
    return AISettings(
        provider=os.getenv("AI_PROVIDER", "fake").strip().lower(),
        base_url=os.getenv("AI_BASE_URL", "").strip(),
        model=os.getenv("AI_MODEL", "").strip(),
        api_key=os.getenv("AI_API_KEY", "").strip(),
        timeout_seconds=_float_env("AI_TIMEOUT_SECONDS", 30, minimum=1, maximum=300),
        max_retries=_int_env("AI_MAX_RETRIES", 2, minimum=0, maximum=5),
    )


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


# Agent 编排
AGENT_MAX_STEPS = _int_env("AGENT_MAX_STEPS", 6, minimum=1, maximum=20)
AGENT_TYC_DAILY_LIMIT = _int_env("AGENT_TYC_DAILY_LIMIT", 80, minimum=1, maximum=1000)
AGENT_TYC_MONTHLY_LIMIT = _int_env(
    "AGENT_TYC_MONTHLY_LIMIT", 900, minimum=1, maximum=10000
)
# 天眼查 MCP 网关（与 AI 平台控制台 API Key 相同，tyc_ 开头）
TYC_API_KEY = os.getenv("TYC_API_KEY", "").strip()
TYC_MCP_ENDPOINT = os.getenv(
    "TYC_MCP_ENDPOINT", "https://mcp.tianyancha.com/v1"
).strip()


# Scheduler 定时任务配置（cron 表达式，5 段：分 时 日 月 周）
SCHEDULER_COLLECT_CRON = os.getenv("SCHEDULER_COLLECT_CRON", "*/30 * * * *").strip()
SCHEDULER_EXPIRE_CRON = os.getenv("SCHEDULER_EXPIRE_CRON", "0 * * * *").strip()
SCHEDULER_CLEANUP_CRON = os.getenv("SCHEDULER_CLEANUP_CRON", "0 3 * * *").strip()


@dataclass(frozen=True)
class RetentionSettings:
    signal_days: int = 90
    event_days: int = 90
    run_days: int = 30


def get_retention_settings() -> RetentionSettings:
    return RetentionSettings(
        signal_days=_int_env("RETENTION_SIGNAL_DAYS", 90, minimum=1, maximum=3650),
        event_days=_int_env("RETENTION_EVENT_DAYS", 90, minimum=1, maximum=3650),
        run_days=_int_env("RETENTION_RUN_DAYS", 30, minimum=1, maximum=3650),
    )
