"""静态维度模块注册入口。

新增监控维度时：在本目录新增模块定义 DIMENSION，并加入 DIMENSION_MODULES，
无需改动引擎核心。维度默认配置在此声明；运行时启停与参数覆盖由
rule_dimension_configs 表驱动（见 registry.load_dimension_configs）。
"""

from app.risks.engine.config import DimensionConfig
from app.risks.engine.dimensions import (
    corporate,
    economic,
    geopolitical,
    industry,
    natural,
    policy,
)

DIMENSION_MODULES = (natural, geopolitical, economic, policy, industry, corporate)


def default_dimensions() -> list[DimensionConfig]:
    return [module.DIMENSION for module in DIMENSION_MODULES]
