from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentSession(Base):
    """一次多轮对话的会话容器。"""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        CheckConstraint(
            "agent_kind IN ('risk_query', 'source_onboarding')",
            name="ck_agent_sessions_agent_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    agent_kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'risk_query'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class AgentMessage(Base):
    """对话消息，含工具调用审计记录。"""

    __tablename__ = "agent_messages"
    __table_args__ = (
        Index("ix_agent_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    # 每条：{"name": str, "arguments": {...}, "result": {...}}
    tool_calls: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[AgentSession] = relationship(back_populates="messages")


class SourceOnboardingDraft(Base):
    """数据源接入过程中的可恢复草稿，不保存明文凭据。"""

    __tablename__ = "source_onboarding_drafts"
    __table_args__ = (
        UniqueConstraint("agent_session_id", name="uq_source_onboarding_drafts_session"),
        Index("ix_source_onboarding_drafts_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    agent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL")
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    actor_id: Mapped[str | None] = mapped_column(Text)
    current_step: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'source_url'")
    )
    answers: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TycUsageRecord(Base):
    """天眼查调用计费记录。

    计费口径（与天眼查 AI 平台一致）：成功且返回有效结果计 1 次；
    报错、全部无结果、系统异常和未配置计 0 次。
    """

    __tablename__ = "tyc_usage_records"
    __table_args__ = (
        Index("ix_tyc_usage_records_called_at", "called_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tool_name: Mapped[str] = mapped_column(Text)
    company_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # success / empty / error / not_configured
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
