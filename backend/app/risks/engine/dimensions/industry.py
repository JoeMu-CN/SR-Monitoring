"""E 产业与供应市场维度。

关键原材料/零部件短缺、技术断供、产能与集中度、物流与运输中断、
劳动力市场。以产品柱与行业柱表达"行业/原材料 → 供应商"的传导，
保留主体与地点柱以复现现状（logistics 事件测试使用产品匹配）。
"""

from app.risks.engine.config import (
    COLUMN_ENTITY,
    COLUMN_INDUSTRY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
    DimensionConfig,
)

DIMENSION = DimensionConfig(
    key="industry",
    label="产业与供应市场",
    description="原材料短缺、技术断供、产能变化、物流中断、劳动力市场。",
    event_types=("logistics",),
    match_columns=(COLUMN_ENTITY, COLUMN_LOCATION, COLUMN_PRODUCT, COLUMN_INDUSTRY),
    scoring_overrides={"association_scores": {"industry": 12}},
)
