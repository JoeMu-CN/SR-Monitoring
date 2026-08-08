"""B 地缘政治与安全维度。

武装冲突、制裁与出口管制、政治不稳定、双边关系、公共安全事件。
宏观事件往往没有具体主体、没有精确地点，因此叠加国家柱与行业柱，
把国别/行业级风险挂到供应商。国家柱弱关联起步（8 分），命中
sanctions_country_hit 强制规则（国家或主体命中）时升级 P1。
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
    key="geopolitical",
    label="地缘政治与安全",
    description="武装冲突、制裁与出口管制、政局动荡、双边关系、公共安全事件。",
    event_types=("geopolitical",),
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
            name="sanctions_geopolitical_entity_hit",
            description="制裁/地缘事件主体精确命中供应商",
            event_types=("geopolitical",),
            match_types=("registry_no", "legal_name", "alias"),
            forced_level="P1",
            reason="供应商主体受制裁/地缘事件直接命中，强制提升为 P1",
            event_subtypes=("sanctions",),
        ),
    ),
)
