"""F 供应商主体维度。

承接现有最强匹配逻辑：供应商经营、司法、合规与安全生产风险，
以主体（注册号/法人全称/别名）精确匹配为核心。默认不声明任何增量，
完全沿用全局默认评分（含 sanctions_entity_hit 强制规则、弱关联 P2 上限、
90 天失效），保证既有测试与数据兼容。
"""

from app.risks.engine.config import DEFAULT_COLUMNS, DimensionConfig, DimensionDataSource

DIMENSION = DimensionConfig(
    key="corporate",
    label="供应商主体",
    description="供应商经营、司法、合规与安全生产风险，主体精确匹配为核心。",
    event_types=("corporate", "judicial", "compliance", "other"),
    content_items=(
        "经营与财务异常",
        "司法案件与失信",
        "监管与合规违规",
        "安全生产",
        "主体信息变更",
    ),
    data_sources=(
        DimensionDataSource("tianyancha", "天眼查", "external_tool"),
        DimensionDataSource("credit-china", "信用中国", "planned"),
        DimensionDataSource("court-public-info", "人民法院公告与执行信息", "planned"),
        DimensionDataSource("samr-public-info", "市场监管总局", "planned"),
        DimensionDataSource("mem-incident-bulletin", "应急管理部", "planned"),
    ),
    match_columns=DEFAULT_COLUMNS,
)
