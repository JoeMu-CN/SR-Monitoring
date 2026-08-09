"""A 自然环境维度。

气象灾害、地质灾害、公共卫生事件、环境事故、能源约束。
以地点匹配为核心（事件坐标+影响半径 / 省市区文本），同时保留主体与
产品柱以复现现状（现有 weather 事件测试同时使用主体与地点匹配）。
评分沿用全局默认（弱关联 P2 上限），不新增强制规则以免误伤既有用例。
"""

from app.risks.engine.config import DEFAULT_COLUMNS, DimensionConfig, DimensionDataSource

DIMENSION = DimensionConfig(
    key="natural",
    label="自然环境",
    description="气象/地质灾害、疫情、环境与安全事故、能源约束，地点匹配为核心。",
    event_types=("weather", "geological"),
    content_items=(
        "天气与气象预警",
        "地震与地质灾害",
        "突发自然灾害",
        "公共卫生事件",
        "环境、安全与能源事件",
    ),
    data_sources=(
        DimensionDataSource("nmc-weather", "中央气象台", "connected"),
        DimensionDataSource("mem-incident-bulletin", "应急管理部", "planned"),
        DimensionDataSource("cenc-earthquake", "中国地震台网", "planned"),
        DimensionDataSource("nhc-cdc", "国家卫健委 / 中国疾控中心", "planned"),
    ),
    match_columns=DEFAULT_COLUMNS,
)
