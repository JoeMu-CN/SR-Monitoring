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


@dataclass(frozen=True)
class SearchSettings:
    provider: str
    api_key: str
    base_url: str
    timeout_seconds: float
    monthly_limit: int = 1000


@dataclass(frozen=True)
class Crawl4AISettings:
    enabled: bool
    base_url: str
    api_token: str
    timeout_seconds: float


def get_ai_settings() -> AISettings:
    return AISettings(
        provider=os.getenv("AI_PROVIDER", "fake").strip().lower(),
        base_url=os.getenv("AI_BASE_URL", "").strip(),
        model=os.getenv("AI_MODEL", "").strip(),
        api_key=os.getenv("AI_API_KEY", "").strip(),
        timeout_seconds=_float_env("AI_TIMEOUT_SECONDS", 30, minimum=1, maximum=300),
        max_retries=_int_env("AI_MAX_RETRIES", 2, minimum=0, maximum=5),
    )


def get_search_settings() -> SearchSettings:
    """读取研究搜索配置；缺少 Provider 或 Key 时保持未配置。"""
    return SearchSettings(
        provider=os.getenv("SEARCH_PROVIDER", "none").strip().lower(),
        api_key=os.getenv("SEARCH_API_KEY", "").strip(),
        base_url=os.getenv("SEARCH_BASE_URL", "").strip(),
        timeout_seconds=_float_env("SEARCH_TIMEOUT_SECONDS", 15, minimum=1, maximum=60),
        monthly_limit=_int_env("SEARCH_MONTHLY_LIMIT", 1000, minimum=1, maximum=100000),
    )


def get_crawl4ai_settings() -> Crawl4AISettings:
    """读取受控单页 Crawl4AI 回退配置；默认关闭，避免隐式启动浏览器出网。"""
    return Crawl4AISettings(
        enabled=os.getenv("RESEARCH_CRAWL4AI_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        base_url=os.getenv("RESEARCH_CRAWL4AI_BASE_URL", "http://crawl4ai:11235").strip(),
        api_token=os.getenv("RESEARCH_CRAWL4AI_API_TOKEN", "").strip(),
        timeout_seconds=_float_env("RESEARCH_CRAWL4AI_TIMEOUT_SECONDS", 120, minimum=5, maximum=180),
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


RESEARCH_BOCHA_MONTHLY_BATCH_LIMIT = _int_env(
    "RESEARCH_BOCHA_MONTHLY_BATCH_LIMIT", 450, minimum=0, maximum=100000
)
RESEARCH_BOCHA_MANUAL_RESERVE = _int_env(
    "RESEARCH_BOCHA_MANUAL_RESERVE", 300, minimum=0, maximum=100000
)
RESEARCH_BOCHA_SAFETY_RESERVE = _int_env(
    "RESEARCH_BOCHA_SAFETY_RESERVE", 250, minimum=0, maximum=100000
)


# Agent 编排
AGENT_MAX_STEPS = _int_env("AGENT_MAX_STEPS", 6, minimum=1, maximum=20)
# 以下四项只保留开发环境旧配置兼容；生产运行时从天眼查数据源记录读取。
AGENT_TYC_DAILY_LIMIT = _int_env("AGENT_TYC_DAILY_LIMIT", 80, minimum=1, maximum=1000)
AGENT_TYC_MONTHLY_LIMIT = _int_env(
    "AGENT_TYC_MONTHLY_LIMIT", 900, minimum=1, maximum=10000
)
# 天眼查 MCP 网关（与 AI 平台控制台 API Key 相同，tyc_ 开头）。
# 生产环境只从数据源控制台读取加密密钥；TYC_API_KEY 仅保留开发环境兼容回退。
TYC_API_KEY = os.getenv("TYC_API_KEY", "").strip()
TYC_MCP_ENDPOINT = os.getenv(
    "TYC_MCP_ENDPOINT", "https://mcp.tianyancha.com/v1"
).strip()
# 数据源控制台运行密钥的加密密钥（Fernet base64 32 字节）；
# 未配置时由 DATABASE_URL 派生，仅建议开发/内部环境使用。
DATA_SOURCE_SECRET_KEY = os.getenv("DATA_SOURCE_SECRET_KEY", "").strip()

# 认证与会话（平台本地账号 + 密码 + 服务端会话，不使用企业 OIDC / IdP）
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
_SESSION_SECRET_ENV = os.getenv("SESSION_SECRET", "").strip()
SESSION_SECRET = _SESSION_SECRET_ENV or (
    DATA_SOURCE_SECRET_KEY or "dev-insecure-session-secret-change-me"
)
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "srm_session").strip()
CSRF_COOKIE_NAME = f"{SESSION_COOKIE_NAME}_csrf"
SESSION_IDLE_TIMEOUT_MINUTES = _int_env(
    "SESSION_IDLE_TIMEOUT_MINUTES", 30, minimum=1, maximum=1440
)
SESSION_ABSOLUTE_TIMEOUT_HOURS = _int_env(
    "SESSION_ABSOLUTE_TIMEOUT_HOURS", 8, minimum=1, maximum=720
)
SESSION_SECURE_COOKIE = (
    os.getenv("SESSION_SECURE_COOKIE", "false").strip().lower() in {"1", "true", "yes", "on"}
)
# 首位平台管理员引导：仅当库内无用户时生效；创建后应从配置移除，避免弱口令长期留存。
BOOTSTRAP_ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip()
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
# 跨域来源白名单；配置后仅允许名单内 Origin 的写请求，否则要求同源。
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]


def get_tyc_env_fallback() -> str:
    """返回仅供非生产环境兼容使用的天眼查环境变量密钥。"""
    return TYC_API_KEY if APP_ENV != "production" else ""


def get_tyc_endpoint_fallback() -> str:
    """返回仅供非生产环境兼容使用的天眼查端点环境变量。"""
    return TYC_MCP_ENDPOINT if APP_ENV != "production" else ""


def get_tyc_budget_fallback() -> tuple[int, int]:
    """返回仅供非生产环境兼容使用的天眼查额度环境变量。"""
    if APP_ENV == "production":
        return 80, 900
    return AGENT_TYC_DAILY_LIMIT, AGENT_TYC_MONTHLY_LIMIT


def validate_auth_config() -> None:
    """生产环境认证配置必须显式、独立且只通过 HTTPS 使用。"""
    if APP_ENV != "production":
        return
    if len(_SESSION_SECRET_ENV) < 32:
        raise RuntimeError("生产环境 SESSION_SECRET 必须显式配置且不少于 32 个字符")
    if len(DATA_SOURCE_SECRET_KEY) < 32:
        raise RuntimeError("生产环境 DATA_SOURCE_SECRET_KEY 必须显式配置且不少于 32 个字符")
    if DATA_SOURCE_SECRET_KEY and _SESSION_SECRET_ENV == DATA_SOURCE_SECRET_KEY:
        raise RuntimeError("生产环境 SESSION_SECRET 必须与 DATA_SOURCE_SECRET_KEY 分离")
    if not SESSION_SECURE_COOKIE:
        raise RuntimeError("生产环境 SESSION_SECURE_COOKIE 必须为 true")
    if not ALLOWED_ORIGINS or any(
        not origin.startswith("https://") for origin in ALLOWED_ORIGINS
    ):
        raise RuntimeError("生产环境 ALLOWED_ORIGINS 必须配置为 HTTPS 来源")


# Scheduler 定时任务配置（cron 表达式，5 段：分 时 日 月 周）
SCHEDULER_COLLECT_CRON = os.getenv("SCHEDULER_COLLECT_CRON", "*/30 * * * *").strip()
SCHEDULER_EXPIRE_CRON = os.getenv("SCHEDULER_EXPIRE_CRON", "0 * * * *").strip()
SCHEDULER_CLEANUP_CRON = os.getenv("SCHEDULER_CLEANUP_CRON", "0 3 * * *").strip()
# 研究轨发布开关：本地默认开启以保留开发/测试能力，生产发布候选必须显式关闭。
RESEARCH_TRACK_ENABLED = os.getenv("RESEARCH_TRACK_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# 研究轨只由 Scheduler 创建到期任务；主题或归属管理员留空时对应调度停用。
RESEARCH_SCHEDULE_OWNER_USERNAME = os.getenv("RESEARCH_SCHEDULE_OWNER_USERNAME", "").strip()
RESEARCH_DAILY_CRON = os.getenv("RESEARCH_DAILY_CRON", "0 8 * * *").strip()
RESEARCH_DAILY_TOPIC = os.getenv("RESEARCH_DAILY_TOPIC", "").strip()
RESEARCH_WEEKLY_CRON = os.getenv("RESEARCH_WEEKLY_CRON", "30 8 * * mon").strip()
RESEARCH_WEEKLY_TOPIC = os.getenv("RESEARCH_WEEKLY_TOPIC", "").strip()
RESEARCH_MONTHLY_ENABLED = os.getenv("RESEARCH_MONTHLY_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RESEARCH_MONTHLY_CRON = os.getenv("RESEARCH_MONTHLY_CRON", "0 9 1 * *").strip()
RESEARCH_MONTHLY_TOPIC = os.getenv("RESEARCH_MONTHLY_TOPIC", "").strip()
RESEARCH_MONTHLY_SUPPLIER_SCOPE = os.getenv(
    "RESEARCH_MONTHLY_SUPPLIER_SCOPE", "limited"
).strip().lower()
if RESEARCH_MONTHLY_SUPPLIER_SCOPE not in {"limited", "all"}:
    raise RuntimeError("RESEARCH_MONTHLY_SUPPLIER_SCOPE 仅支持 limited 或 all")
RESEARCH_MONTHLY_SUPPLIER_LIMIT = _int_env(
    "RESEARCH_MONTHLY_SUPPLIER_LIMIT", 100, minimum=1, maximum=100
)
# 本地研究 Worker 默认关闭。当前仅提供不访问外网/Provider/模型的生命周期测试模式。
RESEARCH_WORKER_ENABLED = os.getenv("RESEARCH_WORKER_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RESEARCH_WORKER_MODE = os.getenv("RESEARCH_WORKER_MODE", "local_lifecycle_test").strip()
RESEARCH_ORCHESTRATOR = os.getenv("RESEARCH_ORCHESTRATOR", "legacy").strip().lower()
if RESEARCH_ORCHESTRATOR not in {"legacy", "langgraph"}:
    raise RuntimeError("RESEARCH_ORCHESTRATOR 仅支持 legacy 或 langgraph")
RESEARCH_WORKER_POLL_SECONDS = _float_env(
    "RESEARCH_WORKER_POLL_SECONDS", 5, minimum=0.1, maximum=60
)
RESEARCH_WORKER_LEASE_SECONDS = _int_env(
    "RESEARCH_WORKER_LEASE_SECONDS", 300, minimum=30, maximum=3600
)
RESEARCH_WORKER_HEARTBEAT_INTERVAL_SECONDS = _int_env(
    "RESEARCH_WORKER_HEARTBEAT_INTERVAL_SECONDS", 15, minimum=5, maximum=300
)
RESEARCH_WORKER_HEARTBEAT_STALE_SECONDS = _int_env(
    "RESEARCH_WORKER_HEARTBEAT_STALE_SECONDS", 60, minimum=10, maximum=900
)
RESEARCH_TOOL_RUN_STALE_SECONDS = _int_env(
    "RESEARCH_TOOL_RUN_STALE_SECONDS", 1800, minimum=60, maximum=86400
)
# 每次定时任务对积压信号执行 AI 解析的批大小；每条信号一次独立 LLM 调用
SIGNAL_ANALYZE_BATCH = _int_env(
    "SIGNAL_ANALYZE_BATCH", 20, minimum=1, maximum=500
)
# LLM 解析前的确定性相关性预过滤开关（保守：拿不准一律放行）
SIGNAL_RELEVANCE_FILTER_ENABLED = os.getenv(
    "SIGNAL_RELEVANCE_FILTER_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}


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
