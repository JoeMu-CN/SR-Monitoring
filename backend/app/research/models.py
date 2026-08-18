"""研究任务、来源、引用和原子结论的持久化模型。"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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
            "task_type IN ('manual', 'daily', 'weekly', 'monthly')",
            name="ck_research_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', "
            "'skipped', 'budget_exhausted')",
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
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("research_batches.id", ondelete="CASCADE")
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
    graph_version: Mapped[str | None] = mapped_column(Text)
    checkpoint_thread_id: Mapped[str | None] = mapped_column(Text)
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
    events: Mapped[list["ResearchTaskEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    tool_runs: Mapped[list["ResearchToolRun"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )
    batch: Mapped["ResearchBatch | None"] = relationship(back_populates="tasks")


class ResearchBatch(Base):
    """周期研究批次；只负责供应商快照、子任务扇出和状态汇总。"""

    __tablename__ = "research_batches"
    __table_args__ = (
        CheckConstraint(
            "period_type IN ('monthly', 'weekly')",
            name="ck_research_batches_period_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed', "
            "'cancelled', 'capacity_blocked', 'budget_exhausted')",
            name="ck_research_batches_status",
        ),
        UniqueConstraint(
            "owner_user_id", "period_type", "period_key",
            name="uq_research_batches_owner_period",
        ),
        Index("ix_research_batches_status_period", "status", "period_type", "period_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period_type: Mapped[str] = mapped_column(Text, nullable=False)
    period_key: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date] = mapped_column(nullable=False)
    period_end: Mapped[date] = mapped_column(nullable=False)
    topic: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    supplier_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    supplier_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    running_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    budget_exhausted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    budget_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    graph_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list[ResearchTask]] = relationship(back_populates="batch")
    reports: Mapped[list["ResearchReport"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", passive_deletes=True
    )


class ResearchProviderQuotaPeriod(Base):
    """Provider 自然月额度账本；预占和结算必须在数据库事务内完成。"""

    __tablename__ = "research_provider_quota_periods"
    __table_args__ = (
        CheckConstraint(
            "monthly_limit > 0 AND scheduled_reserved >= 0 "
            "AND manual_reserved >= 0 AND used >= 0",
            name="ck_research_provider_quota_nonnegative",
        ),
        UniqueConstraint(
            "provider", "period_key", name="uq_research_provider_quota_period"
        ),
        Index("ix_research_provider_quota_period", "provider", "period_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    period_key: Mapped[str] = mapped_column(Text, nullable=False)
    monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    manual_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ResearchScheduleConfig(Base):
    """管理员维护的周期研究配置；开关与批准记录和执行批次分离。"""

    __tablename__ = "research_schedule_configs"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('weekly', 'monthly')",
            name="ck_research_schedule_configs_type",
        ),
        CheckConstraint(
            "approved_monthly_quota IS NULL OR approved_monthly_quota > 0",
            name="ck_research_schedule_configs_quota",
        ),
        UniqueConstraint("schedule_type", name="uq_research_schedule_configs_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    schedule_type: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cron_expression: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'30 8 * * mon'")
    )
    topic_template: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    budget_template: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    approved_monthly_quota: Mapped[int | None] = mapped_column(Integer)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_note: Mapped[str | None] = mapped_column(Text)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ResearchWorkerHeartbeat(Base):
    """研究 Worker 的最新运行心跳；异常退出时由 last_seen_at 判断过期。"""

    __tablename__ = "research_worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('online', 'stopped')",
            name="ck_research_worker_heartbeats_status",
        ),
        UniqueConstraint("worker_id", name="uq_research_worker_heartbeats_worker"),
        Index("ix_research_worker_heartbeats_last_seen", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    worker_id: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    orchestrator: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'online'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchTaskEvent(Base):
    """供任务回放和可视化使用的脱敏只追加事件。"""

    __tablename__ = "research_task_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'info')",
            name="ck_research_task_events_status",
        ),
        Index("ix_research_task_events_task_id_id", "task_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    node_key: Mapped[str] = mapped_column(Text, nullable=False)
    parent_node_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[ResearchTask] = relationship(back_populates="events")


class ResearchToolRun(Base):
    """外部工具调用的脱敏幂等账本，避免恢复时重复产生费用。"""

    __tablename__ = "research_tool_runs"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('web_search', 'public_page_read', 'report_generation')",
            name="ck_research_tool_runs_action_type",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_research_tool_runs_status",
        ),
        UniqueConstraint("task_id", "action_id", name="uq_research_tool_runs_task_action"),
        Index("ix_research_tool_runs_task_status", "task_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False
    )
    action_id: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    usage_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result_reference: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_category: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[ResearchTask] = relationship(back_populates="tool_runs")


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
            "(task_id IS NULL) <> (batch_id IS NULL)",
            name="ck_research_reports_task_or_batch",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_research_reports_review_status",
        ),
        Index("ix_research_reports_task_created", "task_id", "created_at"),
        Index("ix_research_reports_batch_created", "batch_id", "created_at"),
        Index(
            "uq_research_reports_batch_id",
            "batch_id",
            unique=True,
            postgresql_where=text("batch_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=True
    )
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("research_batches.id", ondelete="CASCADE"), nullable=True
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

    task: Mapped[ResearchTask | None] = relationship(back_populates="reports")
    batch: Mapped[ResearchBatch | None] = relationship(back_populates="reports")
