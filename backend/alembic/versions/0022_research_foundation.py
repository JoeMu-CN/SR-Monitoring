"""研究任务、来源、引用和原子结论基础模型。

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_tasks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_type", sa.Text(), server_default="manual", nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("supplier_scope", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "task_type IN ('manual', 'daily', 'weekly')",
            name="ck_research_tasks_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_research_tasks_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_research_tasks_owner_idempotency"
        ),
    )
    op.create_index("ix_research_tasks_status_created", "research_tasks", ["status", "created_at"])
    op.create_index("ix_research_tasks_lease_until", "research_tasks", ["lease_until"])

    op.create_table(
        "research_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("research_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), server_default="web", nullable=False),
        sa.Column("credibility_tier", sa.Text(), server_default="unrated", nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("content_excerpt", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_sources_task_retrieved", "research_sources", ["task_id", "retrieved_at"]
    )

    op.create_table(
        "research_citations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("research_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "research_claims",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("research_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), server_default="unverified", nullable=False),
        sa.Column("confidence", sa.SmallInteger(), nullable=True),
        sa.Column(
            "promoted_signal_id",
            sa.BigInteger(),
            sa.ForeignKey("raw_signals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "claim_type IN ('fact', 'inference', 'forecast')",
            name="ck_research_claims_claim_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("promoted_signal_id"),
    )
    op.create_index("ix_research_claims_task", "research_claims", ["task_id"])

    op.create_table(
        "research_claim_citations",
        sa.Column(
            "claim_id",
            sa.BigInteger(),
            sa.ForeignKey("research_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "citation_id",
            sa.BigInteger(),
            sa.ForeignKey("research_citations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("claim_id", "citation_id"),
    )
    op.create_index(
        "ix_research_claim_citations_citation", "research_claim_citations", ["citation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_research_claim_citations_citation", table_name="research_claim_citations")
    op.drop_table("research_claim_citations")
    op.drop_index("ix_research_claims_task", table_name="research_claims")
    op.drop_table("research_claims")
    op.drop_table("research_citations")
    op.drop_index("ix_research_sources_task_retrieved", table_name="research_sources")
    op.drop_table("research_sources")
    op.drop_index("ix_research_tasks_lease_until", table_name="research_tasks")
    op.drop_index("ix_research_tasks_status_created", table_name="research_tasks")
    op.drop_table("research_tasks")
