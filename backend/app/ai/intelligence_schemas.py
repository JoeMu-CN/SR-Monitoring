"""情报报告的 LLM 输入/输出契约（简要报告 + 风险提醒建议）。

与 ``app.ai.schemas``（信号解析）严格分离：本模块只服务于「情报聚合」页面，
不改动既有信号解析 Schema，避免两条独立的 LLM 契约互相污染。

``IntelligenceOutput`` 是提示词与落库之间的唯一机器契约：提示词内嵌它的 JSON
Schema，落库前用它校验，页面只渲染它的两个字段。suggestions 固定 1-3 条，
对应产品要求的「风险提醒建议 1–3 项」；超出范围的返回做确定性降级（见
``_normalize_suggestions``），而不是让整条报告因条数不合法而作废。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 本提示词的版本标识；落库到 intelligence_reports.prompt_version，便于回溯口径变化。
INTELLIGENCE_PROMPT_VERSION = "intelligence-report-v1"

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