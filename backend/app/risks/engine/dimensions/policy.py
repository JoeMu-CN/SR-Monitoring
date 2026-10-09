"""D 政策与法规维度（预留，可在工作台激活）。

行业监管趋严、进出口政策、供应链合规法规（EU CSDDD / CBAM / 美国 UFLPA）、
劳动与税收法规。以行业柱与产品柱为核心抓手。

说明：现有 AI 事件类型枚举粒度较粗（监管/合规类事件暂归入 compliance/
trade_policy），因此本维度声明 ``event_types=()`` 且 ``enabled=False``，作为
可运行时启用的预留维度，不占用任何 AI 事件类型。启用方式为在信息源控制台把
本维度置为 enabled（写入 rule_dimension_configs），无需新增 AI 事件类型：
引擎采用"信源优先"路由，本维度声明的政策类信源（customs-announcement、
eu-official-journal、eu-compliance、uflpa-entity-list、mee-announcement）产出
的信号按 source_code 直接归属本维度，不受 AI 粗事件类型影响。归属策略只决定
"由哪个维度评分"，不会让无相关性的信号凭空产生风险提醒。
"""

from app.risks.engine.config import (
    COLUMN_COUNTRY,
    COLUMN_INDUSTRY,
    COLUMN_PRODUCT,
    DimensionConfig,
    DimensionDataSource,
)
from app.risks.scoring import ForcedRule

DIMENSION = DimensionConfig(
    key="policy",
    label="政策与法规",
    description="行业监管、进出口政策、供应链合规法规、劳动与税收法规（预留维度）。",
    event_types=(),
    content_items=("行业监管", "进出口政策", "供应链合规法规", "劳动法规", "税收法规"),
    data_sources=(
        DimensionDataSource("customs-announcement", "海关总署公告", "connected"),
        DimensionDataSource("eu-official-journal", "欧盟官方公报 EUR-Lex", "connected"),
        DimensionDataSource("eu-compliance", "欧盟供应链合规法规", "connected"),
        DimensionDataSource("uflpa-entity-list", "美国 UFLPA 实体清单", "connected"),
        DimensionDataSource("mee-announcement", "生态环境部公告", "connected"),
    ),
    match_columns=(COLUMN_INDUSTRY, COLUMN_PRODUCT, COLUMN_COUNTRY),
    enabled=False,
    scoring_overrides={"association_scores": {"country": 8, "industry": 12}},
    forced_rules_add=(
        ForcedRule(
            name="policy_industry_hit",
            description="行业监管/合规政策命中供应商行业",
            event_types=(),
            match_types=("industry",),
            forced_level="P2",
            reason="供应商所在行业受监管/合规政策直接影响，强制提升为 P2",
        ),
    ),
)
