"""维度配置模型。

每个监控维度（自然环境/地缘政治/经济金融/政策法规/产业市场/供应商主体）
是一个独立可维护的静态模块。维度声明自己负责的事件类型、启用的
匹配柱、以及相对全局默认的评分增量与追加的强制规则。

评分三层叠加（在 registry.build_scoring 中合成）：
  全局默认 load_scoring_settings()（含 RISK_SCORING_CONFIG 环境变量）
    → 维度增量 scoring_overrides（dict 键级合并）与 forced_rules_add（追加）
    → DB 覆盖 rule_dimension_configs.config（dict 键级合并，forced_rules 整体替换）

运行时配置语义：
- 新增维度：在 dimensions/ 下新增模块并在 registry 注册，随应用重新发布。
- 启用/禁用与参数调整：存于 rule_dimension_configs 表，引擎每次处理事件时
  重新合并，下一次评分即生效，不修改代码、不重启。
"""

from dataclasses import dataclass, field

from app.risks.scoring import ForcedRule


@dataclass(frozen=True)
class DimensionDataSource:
    """维度引用的数据源；状态用于区分已接入能力与后续规划。"""

    code: str
    name: str
    status: str  # connected / planned / external_tool

# 匹配柱标识
COLUMN_ENTITY = "entity"        # 主体：注册号/法人全称/别名
COLUMN_LOCATION = "location"    # 地点：文本 + PostGIS 空间半径
COLUMN_PRODUCT = "product"      # 供应产品关键词
COLUMN_COUNTRY = "country"      # 国家/区域（宏观维度）
COLUMN_INDUSTRY = "industry"    # 行业/原材料（宏观维度）

ALL_COLUMNS: tuple[str, ...] = (
    COLUMN_ENTITY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
    COLUMN_COUNTRY,
    COLUMN_INDUSTRY,
)

# 默认三柱（复现现状：所有事件类型均做主体+地点+产品匹配）
DEFAULT_COLUMNS: tuple[str, ...] = (COLUMN_ENTITY, COLUMN_LOCATION, COLUMN_PRODUCT)


@dataclass(frozen=True)
class DimensionConfig:
    """单个监控维度的声明式配置（维度相对全局默认的增量）。"""

    key: str
    label: str
    description: str
    event_types: tuple[str, ...]
    content_items: tuple[str, ...] = ()
    data_sources: tuple[DimensionDataSource, ...] = ()
    match_columns: tuple[str, ...] = DEFAULT_COLUMNS
    enabled: bool = True
    # 评分增量：dict 字段（severity_scores/association_scores）按键级合并到
    # 全局默认之上；标量字段（p1_min 等）直接覆盖。不含 forced_rules（用
    # forced_rules_add 追加，避免覆盖全局默认的 sanctions_entity_hit）。
    scoring_overrides: dict[str, object] = field(default_factory=dict)
    # 维度追加的强制规则（在全局默认强制规则之上追加）。
    forced_rules_add: tuple[ForcedRule, ...] = ()

    def handles(self, event_type: str) -> bool:
        return event_type in self.event_types
