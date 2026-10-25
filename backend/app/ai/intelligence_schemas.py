"""情报报告的 LLM 输入/输出契约（简要报告 + 风险提醒建议）。

与 ``app.ai.schemas``（信号解析）严格分离：本模块只服务于「情报聚合」页面，
不改动既有信号解析 Schema，避免两条独立的 LLM 契约互相污染。

``IntelligenceOutput`` 是提示词与落库之间的唯一机器契约：提示词内嵌它的 JSON
Schema，落库前用它校验，页面只渲染它的两个字段。suggestions 固定 1-3 条，
对应产品要求的「风险提醒建议 1–3 项」；超出范围的返回做确定性降级（见
``_normalize_suggestions``），而不是让整条报告因条数不合法而作废。

两段式契约（门控 → 报告）
--------------------------
``IntelligenceGateInput/GateOutput`` 是生成前的相关性门控：只判「这条公告是否
值得进入情报页」，不做解读。门控与报告共用同一 Provider，但输入体积更小（正文
只取前 N 字）、输出只有两个字段；判定为不相关时**不发起报告调用**，以
``status='skipped'`` 落库并在列表隐藏。门控通过后仍走原有
``generate_intelligence_report`` 语义，人工补生成不经门控。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 本提示词的版本标识；落库到 intelligence_reports.prompt_version，便于回溯口径变化。
INTELLIGENCE_PROMPT_VERSION = "intelligence-report-v1"

#: 门控提示词版本；落库到 intelligence_reports.gate_prompt_version，口径调整时递增，
#: 重判脚本以版本差异判定哪些历史行需要重新过门控。
#: v2：关注域剔除通用品类词（添加剂/化工原料/工业耗材），减少字面误命中。
#: v3：加制造业供应链硬门槛，明确「主题/国别单独命中不构成相关」，并配合
#:     ``risks/intelligence_filter.py`` 的确定性规则拦掉食品、检疫、环保程序等噪声。
GATE_PROMPT_VERSION = "intelligence-gate-v3"

#: 门控判定依据的字数上限；超出按确定性规则截断，不让整条判定作废。
GATE_REASON_MAX_CHARS = 200

#: 情报通道覆盖的监控维度键；与维度注册表的 key 完全一致（policy.py / industry.py）。
IntelligenceCategory = Literal["policy", "industry"]

INTELLIGENCE_CATEGORIES: tuple[IntelligenceCategory, ...] = ("policy", "industry")

#: 风险提醒建议的条数上下限（产品约定 1–3 条）。
MIN_SUGGESTIONS = 1
MAX_SUGGESTIONS = 3


class IntelligenceInput(BaseModel):
    """交给 LLM 的单条情报输入；内容来自公开信源文本，本身不可信。"""

    model_config = ConfigDict(extra="forbid")

    event_id: int
    category: IntelligenceCategory
    source_code: str
    source_name: str
    title: str
    summary: str
    content: str
    url: str | None = None
    published_at: datetime | None = None


class IntelligenceOutput(BaseModel):
    """LLM 输出：中文简要报告 + 1–3 条风险提醒建议。"""

    model_config = ConfigDict(extra="forbid")

    report_zh: str = Field(min_length=1, max_length=2000)
    suggestions: list[str] = Field(min_length=MIN_SUGGESTIONS, max_length=MAX_SUGGESTIONS)

    @field_validator("suggestions")
    @classmethod
    def _normalize_suggestions(cls, value: list[str]) -> list[str]:
        """去空去重并截断到上限；至少保留 1 条，否则整条输出视为无效。

        模型偶尔会返回空串或重复项，这与「报告内容无效」不同：按确定性规则就地
        修正即可，不必为条数瑕疵丢弃一份可用的报告。
        """
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            raise ValueError("风险提醒建议不能为空")
        return cleaned[:MAX_SUGGESTIONS]


class IntelligenceScope(BaseModel):
    """门控用的关注域快照（静态维度主题 + 启用供应商画像）。

    空集合是合法输入：画像尚未补全时门控只依赖 ``themes``，由提示词要求
    「拿不准一律放行」，不得因为画像为空就判定为不相关。
    """

    model_config = ConfigDict(extra="forbid")

    themes: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    product_keywords: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    supplier_count: int = 0


class IntelligenceGateInput(BaseModel):
    """交给门控的单条情报输入：事件自身信息 + AI 已提取的产品/行业 + 关注域。"""

    model_config = ConfigDict(extra="forbid")

    event_id: int
    category: IntelligenceCategory
    source_code: str
    source_name: str
    title: str
    summary: str
    content: str
    event_type: str
    event_subtype: str | None = None
    affected_products: list[str] = Field(default_factory=list)
    affected_industries: list[str] = Field(default_factory=list)
    affected_countries: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    scope: IntelligenceScope = Field(default_factory=IntelligenceScope)


class IntelligenceGateOutput(BaseModel):
    """门控输出：是否值得进入情报页 + 一句话中文依据（用于审计与调参）。"""

    model_config = ConfigDict(extra="forbid")

    relevant: bool
    reason_zh: str = Field(min_length=1)

    @field_validator("reason_zh")
    @classmethod
    def _normalize_reason(cls, value: str) -> str:
        """去空并截断到上限：依据过长只做确定性降级，不让整条判定因字数作废。"""
        text = value.strip()
        if not text:
            raise ValueError("门控判定依据不能为空")
        return text[:GATE_REASON_MAX_CHARS]