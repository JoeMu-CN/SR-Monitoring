"""E 产业与供应市场维度。

关键原材料/零部件短缺、技术断供、产能与集中度、物流与运输中断、劳动力市场，
以及市场行情（大宗商品价格、行业指数）。以产品柱与行业柱表达
"行业/原材料/价格行情 → 供应商"的传导，保留主体与地点柱以复现现状
（logistics 事件测试使用产品匹配）。

大宗商品期货行情（commodity-futures）由经济与金融维度迁入本维度：行情类
事件（market）在 AI 侧与"物流/产业"同属供应市场传导，且价格行情按商品与
行业口径表达更准确；半导体指数（semiconductor-index）为行业景气度代表性
价格信源。迁移后 commodity-futures 只被本维度声明，避免归属冲突。
"""

from app.risks.engine.config import (
    COLUMN_ENTITY,
    COLUMN_INDUSTRY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
    DimensionConfig,
    DimensionDataSource,
)

DIMENSION = DimensionConfig(
    key="industry",
    label="产业与供应市场",
    description=(
        "原材料短缺、技术断供、产能变化、物流中断、劳动力市场，"
        "以及市场行情（大宗商品价格、行业指数）。"
    ),
    event_types=("logistics", "market"),
    content_items=(
        "关键原材料与零部件短缺",
        "技术断供",
        "产能与集中度",
        "物流与运输中断",
        "劳动力市场",
        "市场行情（大宗商品价格、行业指数）",
    ),
    data_sources=(
        DimensionDataSource("sse-shipping", "上海航运交易所 CCFI", "connected"),
        DimensionDataSource("commodity-futures", "大宗商品期货行情", "connected"),
        DimensionDataSource("semiconductor-index", "半导体行业指数行情", "connected"),
    ),
    match_columns=(COLUMN_ENTITY, COLUMN_LOCATION, COLUMN_PRODUCT, COLUMN_INDUSTRY),
    scoring_overrides={"association_scores": {"industry": 12}},
)
