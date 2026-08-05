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
