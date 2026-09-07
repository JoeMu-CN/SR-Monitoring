"""通知模块 API 请求/响应结构。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

VALID_CHANNELS = ("dingtalk", "feishu", "serverchan", "pushplus")
VALID_LEVELS = ("P1", "P2", "P3", "P4")
DeliveryStatus = Literal[
    "success",
    "failed",
    "queued",
    "merged",
    "rate_limited",
    "quiet_suppressed",
    "expired_suppressed",
]
_delivery_status_adapter: TypeAdapter[DeliveryStatus] = TypeAdapter(DeliveryStatus)


def parse_delivery_status(value: str) -> DeliveryStatus:
    """将数据库投递状态解析为 API 响应允许的字面量。"""
    return _delivery_status_adapter.validate_python(value)


class SubscriptionUpsert(BaseModel):
    """更新全局订阅配置（级别 / 免打扰）。"""

    push_levels: list[str] = Field(default_factory=lambda: ["P1", "P2"])
    quiet_hours: dict[str, object] | None = None


class ChannelToggle(BaseModel):
    """启停单个渠道（channel 级订阅）。"""

    enabled: bool
    receiver: str | None = None


class SubscriptionRead(BaseModel):
    channel: str
    receiver: str | None = None
    push_levels: list[str]
    quiet_hours: dict[str, object] | None = None
    enabled: bool
    updated_at: datetime | None = None


class TestSendRequest(BaseModel):
    """测试消息内容（标题与正文可选，缺省用默认模板）。"""

    title: str | None = None
    content: str | None = None


class TestSendResult(BaseModel):
    channel: str
    ok: bool
    detail: str


class DeliveryRead(BaseModel):
    id: int
    alert_id: int | None = None
    channel: str
    status: DeliveryStatus
    title: str | None = None
    pushed_level: str | None = None
    attempt: int
    error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime | None = None


class DeliveryListResponse(BaseModel):
    items: list[DeliveryRead]
    total: int
