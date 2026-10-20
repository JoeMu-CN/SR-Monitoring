"""用户级邮件推送模型（《风险提醒邮件推送接入方案.md》第 5 章）。

- user_notification_subscriptions：一人一行的推送方案，是「推送管理」矩阵的唯一数据源。
- user_supplier_assignments：风险分析员负责的供应商，仅 risk_analyst 参与投递过滤。
- notification_monthly_reports：月报投递记录，按 (user_id, period) 幂等并保留审计。

为什么与 models.py 分文件：邮件按用户投递是新增能力，独立文件便于控制单文件规模，
也避免渠道订阅表（notification_subscriptions）与用户方案表语义混淆；三张表同属一个
Base，结构变更统一由迁移 0064 管理。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# 月报状态语义：
# - success         已成功发送
# - failed          重试后最终失败（保留 error 便于排查，不含凭据）
# - skipped_no_data 上月无 P1/P2/P3 提醒，按方案不发信（仍留痕，避免每轮重算）
MONTHLY_REPORT_STATUSES = ("success", "failed", "skipped_no_data")


class UserNotificationSubscription(Base):
    """用户推送方案：四个开关默认全部关闭，避免升级后突然群发。"""

    __tablename__ = "user_notification_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", name="uq_user_notification_subscriptions_user"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # P4 不提供开关：无推送价值，且会让矩阵多一列噪声。
    push_p1: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    push_p2: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    push_p3: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    push_monthly_report: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserSupplierAssignment(Base):
    """用户-供应商归属；供应商被删除时归属自动清理。"""

    __tablename__ = "user_supplier_assignments"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "supplier_id"),
        # 按供应商反查接收人；user_id 前缀已由主键覆盖，无需单列索引。
        Index("ix_user_supplier_assignments_supplier", "supplier_id"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NotificationMonthlyReport(Base):
    """月报投递记录：UNIQUE(user_id, period) 提供幂等，error 不含凭据。"""

    __tablename__ = "notification_monthly_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success','failed','skipped_no_data')",
            name="ck_notification_monthly_reports_status",
        ),
        UniqueConstraint(
            "user_id", "period", name="uq_notification_monthly_reports_user_period"
        ),
        Index(
            "ix_notification_monthly_reports_period_status", "period", "status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 自然月（Asia/Shanghai），格式 YYYY-MM。
    period: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # 失败原因只保留异常类型与 SMTP 返回码，不含用户名/密码/响应体。
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
