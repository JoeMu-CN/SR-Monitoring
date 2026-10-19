"""行情指标注册表（代码内置默认值）。

一个指标 = 一个信息源的一条观察点序列。指标与观察点的绑定键是
``extra_match``：它必须与适配器 ``RawSourceItem.extra`` 的键名/取值逐字一致，
否则 :func:`app.market.evaluation.load_series` 恒空、指标永远停在"数据积累中"
且不报错。因此本文件是纯代码常量，不建表、不从 DB 注入指标本身。

运行时只允许覆盖阈值/窗口/标签与启停（见 :mod:`app.market.config_store`），
``source_code`` / ``extra_match`` / ``value_key`` / ``change_key`` / 事件类型
等绑定身份字段不可配置，避免运行期把读数切到不存在的键上。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover —— 仅类型依赖，避免与 config_store 循环导入
    from app.market.config_store import MarketConfig

FUTURES_SOURCE_CODE: Final = "commodity-futures"
FX_SOURCE_CODE: Final = "fx-rates"
SEMICONDUCTOR_SOURCE_CODE: Final = "semiconductor-index"

# 观察点窗口的合法区间（与运行时覆盖的校验口径一致）
MIN_CHANGE_PCT: Final = 0.0
MAX_CHANGE_PCT: Final = 50.0
MIN_WINDOW: Final = 2
MAX_WINDOW: Final = 60
MIN_OBSERVATIONS: Final = 2

VALID_DIRECTIONS: Final = frozenset({"up", "down", "both"})
VALID_SEVERITIES: Final = frozenset({"critical", "high", "medium", "low"})


@dataclass(frozen=True, slots=True)
class SeverityBand:
    """单档严重性：单日/窗口幅度达到 ``threshold_pct`` 即落入 ``severity``。"""

    threshold_pct: float
    severity: str


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """单个行情指标的完整口径（代码默认值 + 运行时覆盖后的结果同此结构）。

    - ``extra_match``：观察点识别键，取自 ``raw_signals.raw_data["source_extra"]``；
    - ``value_key`` / ``change_key``：观察点里取现值/当日涨跌的键，``change_key``
      为 ``None`` 时由相邻观察点推导单日变化；
    - ``affected_industries`` / ``affected_currencies``：事件落库时的受影响口径，
      汇率类只写结算币种，行情类只写行业词表。
    """

    code: str
    label: str
    source_code: str
    extra_match: Mapping[str, str]
    value_key: str
    change_key: str | None
    unit: str
    event_type: str
    event_subtype: str
    affected_industries: tuple[str, ...]
    affected_currencies: tuple[str, ...]
    direction: str
    sustained_change_pct: float
    sustained_window: int
    shock_change_pct: float
    shock_window: int
    min_observations: int
    severity_bands: tuple[SeverityBand, ...]
    episode_gap: int


FUTURES_BANDS: Final = (
    SeverityBand(15.0, "critical"),
    SeverityBand(10.0, "high"),
    SeverityBand(5.0, "medium"),
)
FX_BANDS: Final = (
    SeverityBand(8.0, "critical"),
    SeverityBand(5.0, "high"),
    SeverityBand(3.0, "medium"),
)
SEMICONDUCTOR_BANDS: Final = (
    SeverityBand(25.0, "critical"),
    SeverityBand(15.0, "high"),
    SeverityBand(8.0, "medium"),
)

# 期货：code / 新浪 symbol / 标签 / 单位 / 受影响行业词表
_FUTURES_TABLE: Final[tuple[tuple[str, str, str, str, tuple[str, ...]], ...]] = (
    (
        "fut-CU", "nf_CU0", "沪铜主力", "元/吨",
        ("铜", "铜材", "铜合金", "电缆", "电线电缆", "漆包线", "铜箔"),
    ),
    (
        "fut-AL", "nf_AL0", "沪铝主力", "元/吨",
        ("铝", "铝材", "铝型材", "铝合金", "压铸"),
    ),
    ("fut-ZN", "nf_ZN0", "沪锌主力", "元/吨", ("锌", "镀锌", "锌合金")),
    (
        "fut-NI", "nf_NI0", "沪镍主力", "元/吨",
        ("镍", "不锈钢", "镍合金", "电池材料"),
    ),
    ("fut-SN", "nf_SN0", "沪锡主力", "元/吨", ("锡", "焊料", "锡材")),
    (
        "fut-PB", "nf_PB0", "沪铅主力", "元/吨",
        ("铅", "铅酸电池", "铅材"),
    ),
    ("fut-AU", "nf_AU0", "沪金主力", "元/克", ("黄金", "贵金属", "金材")),
    ("fut-AG", "nf_AG0", "沪银主力", "元/千克", ("白银", "贵金属", "银浆")),
    (
        "fut-SC", "nf_SC0", "原油主力", "元/桶",
        ("原油", "石化", "炼化", "燃料"),
    ),
    (
        "fut-FU", "nf_FU0", "燃料油主力", "元/吨",
        ("燃料油", "航运", "物流", "船燃"),
    ),
    (
        "fut-RB", "nf_RB0", "螺纹钢主力", "元/吨",
        ("螺纹钢", "钢材", "建筑钢材", "金属结构"),
    ),
    (
        "fut-SS", "nf_SS0", "不锈钢主力", "元/吨",
        ("不锈钢", "钢材", "不锈钢制品"),
    ),
    (
        "fut-TA", "nf_TA0", "PTA 主力", "元/吨",
        ("PTA", "化纤", "聚酯", "纺织原料"),
    ),
    (
        "fut-RU", "nf_RU0", "天然橡胶主力", "元/吨",
        ("天然橡胶", "橡胶", "轮胎", "橡胶制品"),
    ),
    ("fut-CF", "nf_CF0", "棉花主力", "元/吨", ("棉花", "纺织", "棉纱", "面料")),
)

# 汇率：code / ISO 4217 币种 / 标签 / 供应商结算币种所对应的 USD 汇率对货币
_FX_TABLE: Final[tuple[tuple[str, str, str, tuple[str, ...]], ...]] = (
    ("fx-CNY", "CNY", "美元兑人民币汇率", ("USD",)),
    ("fx-JPY", "JPY", "美元兑日元汇率", ("JPY",)),
    ("fx-EUR", "EUR", "美元兑欧元汇率", ("EUR",)),
    ("fx-GBP", "GBP", "美元兑英镑汇率", ("GBP",)),
)


def _futures_spec(
    code: str,
    symbol: str,
    label: str,
    unit: str,
    industries: tuple[str, ...],
) -> IndicatorSpec:
    return IndicatorSpec(
        code=code,
        label=label,
        source_code=FUTURES_SOURCE_CODE,
        extra_match=MappingProxyType({"symbol": symbol}),
        value_key="last_price",
        change_key="change_pct",
        unit=unit,
        event_type="market",
        event_subtype="commodity_price_move",
        affected_industries=industries,
        affected_currencies=(),
        direction="both",
        sustained_change_pct=8.0,
        sustained_window=20,
        shock_change_pct=3.0,
        shock_window=5,
        min_observations=10,
        severity_bands=FUTURES_BANDS,
        episode_gap=5,
    )


def _fx_spec(
    code: str, currency: str, label: str, currencies: tuple[str, ...]
) -> IndicatorSpec:
    return IndicatorSpec(
        code=code,
        label=label,
        source_code=FX_SOURCE_CODE,
        extra_match=MappingProxyType({"currency": currency}),
        value_key="rate",
        change_key=None,  # 汇率接口只有现价，单日变化由相邻观察点推导
        unit="汇率",
        event_type="exchange_rate",
        event_subtype="fx_volatility",
        affected_industries=(),
        affected_currencies=currencies,
        direction="both",
        sustained_change_pct=3.0,
        sustained_window=20,
        shock_change_pct=1.5,
        shock_window=5,
        min_observations=10,
        severity_bands=FX_BANDS,
        episode_gap=5,
    )


SEMICONDUCTOR_SPEC: Final = IndicatorSpec(
    code="semi-980017",
    label="国证半导体芯片指数",
    source_code=SEMICONDUCTOR_SOURCE_CODE,
    extra_match=MappingProxyType({"index_code": "980017"}),
    value_key="last_price",
    change_key="change_pct",
    unit="点位",
    event_type="market",
    event_subtype="industry_market_move",
    affected_industries=(
        "半导体", "芯片", "集成电路", "电子元器件", "被动元件", "PCB",
        "覆铜板", "硅片", "晶圆", "半导体设备", "消费电子", "汽车电子",
    ),  # fmt: skip
    affected_currencies=(),
    direction="both",
    sustained_change_pct=12.0,
    sustained_window=20,
    shock_change_pct=4.0,
    shock_window=5,
    min_observations=10,
    severity_bands=SEMICONDUCTOR_BANDS,
    episode_gap=5,
)

#: 全部内置行情指标（15 期货 + 4 汇率 + 1 半导体指数）。
INDICATORS: Final[tuple[IndicatorSpec, ...]] = (
    *(_futures_spec(*row) for row in _FUTURES_TABLE),
    *(_fx_spec(*row) for row in _FX_TABLE),
    SEMICONDUCTOR_SPEC,
)

INDICATORS_BY_CODE: Final[Mapping[str, IndicatorSpec]] = MappingProxyType(
    {spec.code: spec for spec in INDICATORS}
)


def indicators_for_source(source_code: str) -> tuple[IndicatorSpec, ...]:
    """按信源取全部内置指标（未登记信源返回空元组）。"""
    return tuple(spec for spec in INDICATORS if spec.source_code == source_code)


def _normalize_extra(extra: object) -> Mapping[str, object] | None:
    """把采集读数整理成可比较的映射；缺失/不可解析返回 None。"""
    if isinstance(extra, Mapping):
        return extra
    if isinstance(extra, (str, bytes, bytearray)):
        try:
            parsed = json.loads(extra)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _match_key(expected: str, actual: object) -> bool:
    """单个绑定键的比较：字符串逐字相等；数值容忍 JSONB 取值的字符串化。"""
    if isinstance(actual, str):
        return actual == expected
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        return str(actual) == expected
    return False


def _matches(spec: IndicatorSpec, extra: Mapping[str, object]) -> bool:
    return all(_match_key(expected, extra.get(key)) for key, expected in spec.extra_match.items())


def resolve_indicator(
    source_code: str,
    extra: object,
    config: MarketConfig | None = None,
) -> IndicatorSpec | None:
    """按信源 + 观察点绑定键解析指标；未登记/被停用返回 ``None``。

    ``config`` 缺省时返回代码内置规格；给出运行时配置时返回**合并覆盖后**的规格，
    被配置 ``enabled=false`` 的指标视同未登记返回 ``None``——该信号因此回落既有
    处理路径（不会被行情规则拦截，也不会产生任何行情事件）。
    """
    if not isinstance(source_code, str) or not source_code:
        return None
    normalized = _normalize_extra(extra)
    if normalized is None:
        return None
    for spec in indicators_for_source(source_code):
        if not _matches(spec, normalized):
            continue
        if config is None:
            return spec
        merged = config.spec_for(spec.code)
        if merged is None:
            return None  # 被配置停用：视同未登记
        return merged
    return None