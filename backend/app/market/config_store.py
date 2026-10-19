"""行情指标的运行时配置加载（``rule_dimension_configs`` key=``market-config``）。

与 :func:`app.signals.relevance.load_filter_rules` 同模式：TTL 60 秒缓存 +
显式失效函数；配置读取失败、单字段非法或越界时**逐字段**回退代码默认，
不允许出现"半个指标生效"的中间态。

配置是独立新键，不复用 ``signal-filter``：后者继续服务未登记品种的兜底与既有
过滤链，两者互不影响。覆盖只接受阈值/窗口/标签/方向/分档等运行参数以及
``enabled`` 开关；指标绑定身份（``source_code`` / ``extra_match`` / ``value_key`` /
``change_key`` / 事件类型）不可配置，注册表外的 code 一律忽略，杜绝运行期注入
代码里不存在的指标。

配置示例::

    {"indicators": {
        "fut-CU": {"sustained_change_pct": 6, "shock_window": 4},
        "fx-GBP": {"enabled": False},
    }}
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market.registry import (
    INDICATORS_BY_CODE,
    MAX_CHANGE_PCT,
    MAX_WINDOW,
    MIN_CHANGE_PCT,
    MIN_OBSERVATIONS,
    MIN_WINDOW,
    VALID_DIRECTIONS,
    VALID_SEVERITIES,
    IndicatorSpec,
    SeverityBand,
    resolve_indicator,
)

MARKET_CONFIG_KEY: Final = "market-config"
CONFIG_TTL_SECONDS: Final = 60.0

_INDICATORS_KEY: Final = "indicators"
#: 允许运行时覆盖的字段（绑定身份与事件口径不在其中）
_OVERRIDABLE_FIELDS: Final = (
    "label",
    "direction",
    "sustained_change_pct",
    "sustained_window",
    "shock_change_pct",
    "shock_window",
    "min_observations",
    "episode_gap",
    "severity_bands",
)


def _now() -> float:
    """单调时钟（单点收敛便于测试替换）。"""
    return time.monotonic()


@dataclass(frozen=True, slots=True)
class MarketConfig:
    """合并后的运行时视图：``specs`` 只含注册表内指标，``disabled`` 为停用集合。"""

    specs: Mapping[str, IndicatorSpec]
    disabled: frozenset[str]

    def is_enabled(self, code: str) -> bool:
        """指标是否处于启用状态（未登记或被停用均视为未启用）。"""
        return code in self.specs and code not in self.disabled

    def spec_for(self, code: str) -> IndicatorSpec | None:
        """取合并后的指标规格；未登记或已停用返回 ``None``。"""
        if code in self.disabled:
            return None
        return self.specs.get(code)

    def enabled_specs(self) -> tuple[IndicatorSpec, ...]:
        return tuple(spec for code, spec in self.specs.items() if code not in self.disabled)

    def resolve(self, source_code: str, extra: object) -> IndicatorSpec | None:
        """按信源 + 观察点绑定键解析启用的指标（停用视同未登记）。"""
        return resolve_indicator(source_code, extra, self)


def default_market_config() -> MarketConfig:
    """代码内置默认视图（无任何 DB 覆盖）。"""
    return MarketConfig(specs=INDICATORS_BY_CODE, disabled=frozenset())


def _number(value: object) -> float | None:
    """严格数值：bool 与数字字符串都不接受，避免"看起来合法"的静默误配。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _change_pct(value: object) -> float | None:
    """持续/冲击幅度：必须落在 (0, 50] 开区间内。"""
    number = _number(value)
    if number is None or not MIN_CHANGE_PCT < number <= MAX_CHANGE_PCT:
        return None
    return number


def _window(value: object) -> int | None:
    """窗口点数：必须落在 [2, 60] 闭区间内。"""
    number = _number(value)
    if number is None or not MIN_WINDOW <= number <= MAX_WINDOW:
        return None
    return int(number)


def _min_observations(value: object) -> int | None:
    number = _number(value)
    if number is None or number < MIN_OBSERVATIONS:
        return None
    return int(number)


def _episode_gap(value: object) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _direction(value: object) -> str | None:
    if isinstance(value, str) and value in VALID_DIRECTIONS:
        return value
    return None


def _label(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _severity_bands(value: object) -> tuple[SeverityBand, ...] | None:
    """整体解析分档：任一条目非法即整体回退默认（不部分接受）。"""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    bands: list[SeverityBand] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        severity = item.get("severity")
        if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
            return None
        threshold = _change_pct(item.get("threshold_pct"))
        if threshold is None:
            return None
        bands.append(SeverityBand(threshold, severity))
    ordered = tuple(sorted(bands, key=lambda band: band.threshold_pct, reverse=True))
    if len({band.threshold_pct for band in ordered}) != len(ordered):
        return None
    return ordered


_NUMERIC_PARSERS: Final = {
    "sustained_change_pct": _change_pct,
    "shock_change_pct": _change_pct,
    "sustained_window": _window,
    "shock_window": _window,
    "min_observations": _min_observations,
    "episode_gap": _episode_gap,
    "direction": _direction,
    "label": _label,
    "severity_bands": _severity_bands,
}


def _merge_spec(spec: IndicatorSpec, override: Mapping[str, object]) -> IndicatorSpec:
    """把单指标覆盖合并到默认规格；非法字段逐个丢弃，其余字段照常生效。"""
    changes: dict[str, Any] = {}
    for field in _OVERRIDABLE_FIELDS:
        if field not in override:
            continue
        parsed = _NUMERIC_PARSERS[field](override[field])
        if parsed is None:
            continue
        changes[field] = parsed
    if not changes:
        return spec
    return dataclasses.replace(spec, **changes)


def _merge(defaults: MarketConfig, config: object) -> MarketConfig:
    if not isinstance(config, Mapping):
        return defaults
    overrides = config.get(_INDICATORS_KEY)
    if not isinstance(overrides, Mapping):
        return defaults
    specs = dict(defaults.specs)
    disabled = set(defaults.disabled)
    for code, override in overrides.items():
        if not isinstance(code, str) or code not in INDICATORS_BY_CODE:
            continue  # 注册表外的 code 一律忽略
        if not isinstance(override, Mapping):
            continue
        if override.get("enabled") is False:
            disabled.add(code)
            continue
        specs[code] = _merge_spec(specs[code], override)
    return MarketConfig(specs=MappingProxyType(specs), disabled=frozenset(disabled))


_config_cache: MarketConfig | None = None
_config_cache_until: float = 0.0


def load_market_config(session: Session) -> MarketConfig:
    """读取行情指标运行时配置（DB 覆盖 + 代码默认兜底），TTL 60 秒热更新。

    配置行整体 ``enabled=false``、配置结构非法或读取异常时一律回退代码默认；
    删除配置行即回退默认。
    """
    global _config_cache, _config_cache_until
    now = _now()
    if _config_cache is not None and now < _config_cache_until:
        return _config_cache
    defaults = default_market_config()
    merged = defaults
    try:
        from app.risks.models import RuleDimensionConfig

        row = session.scalar(
            select(RuleDimensionConfig).where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
        )
        if row is not None and row.enabled and isinstance(row.config, dict):
            merged = _merge(defaults, row.config)
    except Exception:  # noqa: BLE001 —— 配置读取失败回退默认（保守安全侧）
        merged = defaults
    _config_cache = merged
    _config_cache_until = now + CONFIG_TTL_SECONDS
    return merged


def invalidate_market_config_cache() -> None:
    """配置更新后立即失效缓存（下次读取重载）。"""
    global _config_cache, _config_cache_until
    _config_cache = None
    _config_cache_until = 0.0