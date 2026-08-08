"""模块化维度规则引擎。

引擎核心（engine.process_event）只负责调度，业务规则在各维度模块
（dimensions/）与评分参数（scoring）中，可独立维护并运行时启停、调参。
"""

from app.risks.engine.config import (
    ALL_COLUMNS,
    COLUMN_COUNTRY,
    COLUMN_ENTITY,
    COLUMN_INDUSTRY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
    DEFAULT_COLUMNS,
    DimensionConfig,
)
from app.risks.engine.engine import (
    event_dedup_key,
    expire_alerts,
    match_suppliers,
    process_event,
)
from app.risks.engine.matching import MATCH_ORDER, MatchCandidate
from app.risks.engine.registry import RuntimeDimension, load_dimensions

__all__ = [
    "ALL_COLUMNS",
    "COLUMN_COUNTRY",
    "COLUMN_ENTITY",
    "COLUMN_INDUSTRY",
    "COLUMN_LOCATION",
    "COLUMN_PRODUCT",
    "DEFAULT_COLUMNS",
    "DimensionConfig",
    "MATCH_ORDER",
    "MatchCandidate",
    "RuntimeDimension",
    "event_dedup_key",
    "expire_alerts",
    "load_dimensions",
    "match_suppliers",
    "process_event",
]
