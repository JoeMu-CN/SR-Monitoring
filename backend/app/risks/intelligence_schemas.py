"""情报聚合接口的响应 Schema（只读；不改动 risks/schemas.py）。

列表项与详情共用同一形状：事件标题/摘要 + 分类 + 信源 + 报告 + 建议 + 生成时间。
只读页面没有编辑动作，因此不存在写入 Payload，避免出现可绕过的写入面。
"""

from datetime import datetime

from pydantic import BaseModel

from app.ai.intelligence_schemas import IntelligenceCategory


class IntelligenceReportRead(BaseModel):
    id: int
    event_id: int
    category: IntelligenceCategory
    status: str
    title: str
    event_summary: str
    event_type: str
    event_subtype: str | None
    event_start_at: datetime | None
    source_code: str
    source_name: str
    source_url: str | None
    published_at: datetime | None
    report_zh: str | None
    suggestions: list[str]
    provider: str
    model: str
    prompt_version: str
    error: str | None
    #: 门控判定依据；仅 skipped 行有值，ready 行恒为 None。
    gate_reason: str | None = None
    generated_at: datetime | None


class IntelligenceReportListResponse(BaseModel):
    items: list[IntelligenceReportRead]
    total: int
    limit: int
    offset: int