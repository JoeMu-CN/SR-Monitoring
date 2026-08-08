"""C 经济与金融维度。

宏观经济、汇率与外汇管制、大宗商品与原材料、货币政策、贸易摩擦。
叠加国家柱与行业柱表达成本/市场传导。贸易管制命中供应商产品或行业时
经 sanctions_product_hit 强制规则升级 P1（如出口管制直接禁售某类产品）。
"""

from app.risks.engine.config import (
    COLUMN_COUNTRY,
    COLUMN_ENTITY,
    COLUMN_INDUSTRY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
    DimensionConfig,
)
from app.risks.scoring import ForcedRule

DIMENSION = DimensionConfig(
    key="economic",
    label="经济与金融",
    description="宏观经济、汇率、大宗商品、货币政策、贸易摩擦与关税。",
    event_types=("trade_policy",),
    match_columns=(
        COLUMN_ENTITY,
        COLUMN_LOCATION,
        COLUMN_PRODUCT,
        COLUMN_COUNTRY,
        COLUMN_INDUSTRY,
    ),
    scoring_overrides={"association_scores": {"country": 8, "industry": 12}},
    forced_rules_add=(
        ForcedRule(
            name="sanctions_product_hit",
            description="贸易管制事件命中供应商产品或行业",
            event_types=("trade_policy",),
            match_types=("product", "industry"),
            forced_level="P1",
            reason="供应商产品/行业受贸易管制直接影响，强制提升为 P1",
            event_subtypes=("sanctions", "export_control"),
        ),
    ),
)
