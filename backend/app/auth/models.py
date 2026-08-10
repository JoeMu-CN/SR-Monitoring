"""认证领域模型：用户、会话与安全审计事件。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

ROLE_CHOICES = ("viewer", "risk_analyst", "risk_admin", "platform_admin")
STATUS_CHOICES = ("pending", "active", "disabled")


class User(Base):
    """平台用户；以 username 唯一识别，密码仅保存 bcrypt 哈希。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer','risk_analyst','risk_admin','platform_admin')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "status IN ('pending','active','disabled')", name="ck_users_status"
        ),
        UniqueConstraint("username"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="viewer")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        onupdate=func.now(),
    )

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    """服务端不透明会话；数据库只保存令牌哈希，浏览器仅持有原文 Cookie。"""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_token_hash", "token_hash"),
        Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 脱敏后的来源信息，禁止写入 IP 完整段、User-Agent 原文或任何凭据
    source_ip_masked: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent_masked: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class SecurityAuditEvent(Base):
    """安全审计事件；记录登录、退出、越权、角色变更、用户停用和会话撤销等。"""

    __tablename__ = "security_audit_events"
    __table_args__ = (Index("ix_security_audit_events_occurred", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ip_masked: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 脱敏后的变更摘要，禁止写入凭据、Token、Cookie 或完整声明
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
