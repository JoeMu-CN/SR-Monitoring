"""D 政策与法规维度（预留，可在工作台激活）。

行业监管趋严、进出口政策、供应链合规法规（EU CSDDD / CBAM / 美国 UFLPA）、
劳动与税收法规。以行业柱与产品柱为核心抓手。

说明：现有 AI 事件类型枚举粒度较粗（监管/合规类事件暂归入 compliance/
trade_policy），因此本维度默认不接管任何现有事件类型、enabled=False，
作为可运行时启用的预留维度。待事件类型细分或新增数据源后，在工作台开启并
指派事件类型即可激活，无需修改引擎核心。
"""

from app.risks.engine.config import (
    COLUMN_COUNTRY,
    COLUMN_INDUSTRY,
    COLUMN_PRODUCT,
    DimensionConfig,
)
from app.risks.scoring import ForcedRule

DIMENSION = DimensionConfig(
    key="policy",
    label="政策与法规",
    description="行业监管、进出口政策、供应链合规法规、劳动与税收法规（预留维度）。",
    event_types=(),
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
