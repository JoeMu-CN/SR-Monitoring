"""F 供应商主体维度。

承接现有最强匹配逻辑：供应商经营、司法、合规与安全生产风险，
以主体（注册号/法人全称/别名）精确匹配为核心。默认不声明任何增量，
完全沿用全局默认评分（含 sanctions_entity_hit 强制规则、弱关联 P2 上限、
90 天失效），保证既有测试与数据兼容。
"""

from app.risks.engine.config import DEFAULT_COLUMNS, DimensionConfig

DIMENSION = DimensionConfig(
    key="corporate",
    label="供应商主体",
    description="供应商经营、司法、合规与安全生产风险，主体精确匹配为核心。",
    event_types=("corporate", "judicial", "compliance", "other"),
    match_columns=DEFAULT_COLUMNS,
)
