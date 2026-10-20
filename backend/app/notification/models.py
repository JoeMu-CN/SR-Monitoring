"""通知订阅与投递记录模型。

- notification_subscriptions：按渠道的订阅配置（级别、免打扰、启停）。
- notification_deliveries：每次投递的审计记录（幂等 + 可回溯）。

与阿里云部署实施清单 1.6「邮件通知模块」共用本表（channel='email'），
用户级推送方案与月报记录见 user_models.py（迁移 0064）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# 投递状态语义：
# - success          发送成功
# - failed           重试后最终失败（审计可见，不静默丢失）
# - queued           P2 等待合并窗口（或 P1 超限转合并）
# - merged           已并入合并摘要（非首条）
# - rate_limited     单渠道限频跳过（记录留痕）
# - quiet_suppressed 免打扰时段跳过（记录留痕）
# - expired_suppressed 有效期已结束，抑制发送并保留审计
DELIVERY_STATUSES = (
    "success",
    "failed",
    "queued",
    "merged",
    "rate_limited",
    "quiet_suppressed",
    "expired_suppressed",
)


class NotificationSubscription(Base):
    __tablename__ = "notification_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # 渠道标识：dingtalk / feishu / serverchan / pushplus / global（全局级别与免打扰）
    channel: Mapped[str] = mapped_column(Text, unique=True)
    receiver: Mapped[str | None] = mapped_column(Text)
    push_levels: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[\"P1\",\"P2\"]'::jsonb")
    )
    quiet_hours: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    # 唯一键拆成两个部分唯一索引（见迁移 0064）：
    # - 固定渠道记录 user_id 恒为 NULL → (alert_id, channel) 去重；
    # - 邮件按用户投递 → (alert_id, channel, user_id) 去重。
    # Postgres 唯一索引不对 NULL 去重，因此不能简单改成三列联合唯一键。
    __table_args__ = (
        CheckConstraint(
            "status IN ('success','failed','queued','merged','rate_limited',"
            "'quiet_suppressed','expired_suppressed')",
            name="ck_notification_deliveries_status",
        ),
        Index(
            "uq_notification_alert_channel_global",
            "alert_id",
            "channel",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_notification_alert_channel_user",
            "alert_id",
            "channel",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index("ix_notification_deliveries_channel_created", "channel", "created_at"),
        Index("ix_notification_deliveries_status_created", "status", "created_at"),
        # 按用户限频与历史查询（与迁移 0064 保持一致，避免 autogenerate 漂移）。
        Index("ix_notification_deliveries_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # alert_id 为 NULL 表示测试消息或合并摘要（多条 queued 合并后的首条记录）
    alert_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("risk_alerts.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(Text)
    # 邮件按用户投递时的收件人；固定 4 渠道恒为 NULL，语义与既有实现一致。
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    # 该 alert 本次投递对应的等级（用于升级重推判断）
    pushed_level: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
