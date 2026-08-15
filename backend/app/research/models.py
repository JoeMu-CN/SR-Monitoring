"""研究任务、来源、引用和原子结论的持久化模型。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ResearchTask(Base):
    """一次可恢复、可取消的异步研究请求。"""

    __tablename__ = "research_tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('manual', 'daily', 'weekly')",
            name="ck_research_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_research_tasks_status",
        ),
        CheckConstraint(
            "search_queries_used >= 0 AND search_results_used >= 0 "
            "AND input_tokens_used >= 0 AND output_tokens_used >= 0 AND cost_amount >= 0",
            name="ck_research_tasks_usage_nonnegative",
        ),
        UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_research_tasks_owner_idempotency"
        ),
        Index("ix_research_tasks_status_created", "status", "created_at"),
        Index("ix_research_tasks_lease_until", "lease_until"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'")
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_scope: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_urls: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    budget_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    search_queries_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    search_results_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    input_tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    output_tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, server_default=text("0")
    )
    current_step: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    execution_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    sources: Mapped[list["ResearchSource"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    claims: Mapped[list["ResearchClaim"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    reports: Mapped[list["ResearchReport"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchSource(Base):
    """研究任务实际读取的来源摘要。"""

    __tablename__ = "research_sources"
    __table_args__ = (Index("ix_research_sources_task_retrieved", "task_id", "retrieved_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'web'"))
    credibility_tier: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unrated'")
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(Text)
    content_excerpt: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[ResearchTask] = relationship(back_populates="sources")
    citations: Mapped[list["ResearchCitation"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchCitation(Base):
    """从来源中截取并回验的引用片段。"""

    __tablename__ = "research_citations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_sources.id", ondelete="CASCADE"), nullable=False
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[ResearchSource] = relationship(back_populates="citations")
    claims: Mapped[list["ResearchClaimCitation"]] = relationship(
        back_populates="citation", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchClaim(Base):
    """研究报告中的一个事实、推断或预测。"""

    __tablename__ = "research_claims"
    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('fact', 'inference', 'forecast')",
            name="ck_research_claims_claim_type",
        ),
        Index("ix_research_claims_task", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    claim_type: Mapped[str] = mapped_column(Text, nullable=False)
    claim_text: Mapped[str] = mapped_column("text", Text, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unverified'")
    )
    confidence: Mapped[int | None] = mapped_column(SmallInteger)
    promoted_signal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_signals.id", ondelete="SET NULL"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[ResearchTask] = relationship(back_populates="claims")
    citations: Mapped[list["ResearchClaimCitation"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchClaimCitation(Base):
    """原子结论与引用之间的结构化关系。"""

    __tablename__ = "research_claim_citations"
    __table_args__ = (Index("ix_research_claim_citations_citation", "citation_id"),)

    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_claims.id", ondelete="CASCADE"), primary_key=True
    )
    citation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("research_citations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    claim: Mapped[ResearchClaim] = relationship(back_populates="citations")
    citation: Mapped[ResearchCitation] = relationship(back_populates="claims")


class ResearchReport(Base):
    """结构化研究报告草稿；发布和结论转正另设人工确认流程。"""

    __tablename__ = "research_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'submitted', 'rejected')",
            name="ck_research_reports_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_research_reports_review_status",
        ),
        Index("ix_research_reports_task_created", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    draft_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'")
    )
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    model_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[ResearchTask] = relationship(back_populates="reports")
