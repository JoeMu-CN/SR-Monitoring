"""行情指标只读视图与运行配置的 API 契约。

两条取舍在这里固化：

1. **响应只含派生指标字段**：``raw_data`` / ``source_extra`` / 指纹 / 正文一律
   不出接口——指标视图是给运营看的判定摘要，不是行情报文通道。
2. **字面量与注册表同源约束**：``direction`` / ``severity`` 的取值范围必须覆盖
   :mod:`app.market.registry` 的 ``VALID_DIRECTIONS`` / ``VALID_SEVERITIES``，
   否则内置指标会写不进 DB 覆盖（测试用 ``TypeAdapter`` 做漂移防护）。

写接口的合并语义：只覆盖补丁里出现且合法的字段，其余沿用当前值（等价于
``app.signals.relevance`` 的 ``signal-filter`` PUT）。越界值与注册表外的指标代码
一律 422，不做静默截断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.market.registry import (
    MAX_CHANGE_PCT,
    MAX_WINDOW,
    MIN_CHANGE_PCT,
    MIN_OBSERVATIONS,
    MIN_WINDOW,
)

#: 指标标签长度上限：注册表标签均为短中文名，超长一律视为手滑输入。
MAX_LABEL_LENGTH = 64

IndicatorStatus = Literal["tripped", "normal", "accumulating"]
Direction = Literal["up", "down", "both"]
Severity = Literal["critical", "high", "medium", "low"]
ConfigSource = Literal["default", "configured"]


class SeverityBandRead(BaseModel):
    """单档严重性；``threshold_pct`` 降序由 ``config_store`` 保证。"""

    model_config = ConfigDict(extra="forbid")

    threshold_pct: float
    severity: Severity


class IndicatorThresholdsRead(BaseModel):
    """指标判定口径（生效值）：持续/冲击双条件 + 最小观察点数 + 严重性分档。"""

    model_config = ConfigDict(extra="forbid")

    sustained_change_pct: float
    sustained_window: int
    shock_change_pct: float
    shock_window: int
    min_observations: int
    direction: Direction
    episode_gap: int
    severity_bands: list[SeverityBandRead]


class IndicatorLatestRead(BaseModel):
    """最近一个观察点；无观察点时三项全空（不用 0 冒充现值）。"""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime | None = None
    value: float | None = None
    change_pct: float | None = None


class IndicatorStatsRead(BaseModel):
    """窗口统计：``sustained_change_pct`` 为累计变动，``shock_max_day_pct`` 为
    方向符合设定范围内的单日最大变动绝对值；不可计算时为 null。"""

    model_config = ConfigDict(extra="forbid")

    observations: int
    sustained_change_pct: float | None = None
    shock_max_day_pct: float | None = None


class IndicatorPointRead(BaseModel):
    """序列上的一个观察点（只给时间与现值，不回传整条原始报文）。"""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    value: float


class IndicatorItemRead(BaseModel):
    """单个行情指标的只读视图。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    source_code: str
    source_name: str | None = None
    unit: str
    latest: IndicatorLatestRead
    stats: IndicatorStatsRead
    thresholds: IndicatorThresholdsRead
    status: IndicatorStatus
    series: list[IndicatorPointRead]
    latest_alert_id: int | None = None


class IndicatorListResponse(BaseModel):
    """指标列表：整体生成时间 + 启用中的指标（停用视同未登记，不在此列出）。"""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    items: list[IndicatorItemRead]


class MarketConfigIndicatorRead(BaseModel):
    """单个指标的生效配置与默认值对照。

    ``overridden`` 为"生效值与代码默认不同（含停用）"，因此写入与默认值相同的
    补丁不会被虚报成覆盖。
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    source_code: str
    unit: str
    enabled: bool
    overridden: bool
    effective: IndicatorThresholdsRead
    defaults: IndicatorThresholdsRead


class MarketConfigRead(BaseModel):
    """当前生效的行情配置；``source`` 只在配置行存在且启用时才是 ``configured``。

    行存在但 ``enabled=false`` 时代码默认才是真正生效的那一份，如实报 ``default``。
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    source: ConfigSource
    updated_at: datetime | None = None
    indicators: list[MarketConfigIndicatorRead]


class SeverityBandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold_pct: float = Field(gt=MIN_CHANGE_PCT, le=MAX_CHANGE_PCT)
    severity: Severity


class MarketConfigIndicatorPatch(BaseModel):
    """单指标覆盖补丁：只出现且合法的字段才会覆盖，其余沿用当前值。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    label: str | None = Field(default=None, min_length=1, max_length=MAX_LABEL_LENGTH)
    direction: Direction | None = None
    sustained_change_pct: float | None = Field(
        default=None, gt=MIN_CHANGE_PCT, le=MAX_CHANGE_PCT
    )
    sustained_window: int | None = Field(default=None, ge=MIN_WINDOW, le=MAX_WINDOW)
    shock_change_pct: float | None = Field(
        default=None, gt=MIN_CHANGE_PCT, le=MAX_CHANGE_PCT
    )
    shock_window: int | None = Field(default=None, ge=MIN_WINDOW, le=MAX_WINDOW)
    min_observations: int | None = Field(default=None, ge=MIN_OBSERVATIONS)
    episode_gap: int | None = Field(default=None, ge=0)
    severity_bands: list[SeverityBandInput] | None = None

    @field_validator("label")
    @classmethod
    def _label_not_blank(cls, value: str | None) -> str | None:
        """纯空白标签 ``config_store`` 会整字段丢弃，接口层直接拒掉。"""
        if value is None:
            return None
        if not value.strip():
            raise ValueError("指标标签不能为空白")
        return value.strip()

    @field_validator("severity_bands")
    @classmethod
    def _bands_threshold_unique(
        cls, value: list[SeverityBandInput] | None
    ) -> list[SeverityBandInput] | None:
        """阈值重复的分档在 ``config_store`` 会被整体丢弃，这里提前 422。"""
        if value is None:
            return None
        thresholds = [band.threshold_pct for band in value]
        if len(set(thresholds)) != len(thresholds):
            raise ValueError("严重性分档阈值不得重复")
        return value


class MarketConfigUpdate(BaseModel):
    """配置写入载荷：按指标代码给出补丁；空字典是 no-op，不清空既有覆盖。"""

    model_config = ConfigDict(extra="forbid")

    indicators: dict[str, MarketConfigIndicatorPatch]