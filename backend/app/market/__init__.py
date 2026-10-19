"""行情监控（market）：指标注册表 + 运行时配置。

对外只暴露三件事：内置指标表（:data:`INDICATORS`）、单条观察点解析
（:func:`resolve_indicator`）与运行时配置视图（:func:`load_market_config`）。
"""

from app.market.config_store import (
    MARKET_CONFIG_KEY,
    MarketConfig,
    default_market_config,
    invalidate_market_config_cache,
    load_market_config,
)
from app.market.registry import (
    INDICATORS,
    INDICATORS_BY_CODE,
    IndicatorSpec,
    SeverityBand,
    indicators_for_source,
    resolve_indicator,
)

__all__ = [
    "INDICATORS",
    "INDICATORS_BY_CODE",
    "MARKET_CONFIG_KEY",
    "IndicatorSpec",
    "MarketConfig",
    "SeverityBand",
    "default_market_config",
    "indicators_for_source",
    "invalidate_market_config_cache",
    "load_market_config",
    "resolve_indicator",
]