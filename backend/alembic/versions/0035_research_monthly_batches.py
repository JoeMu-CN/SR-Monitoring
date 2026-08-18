"""新增全供应商周期研究批次和 monthly 子任务关联。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_type", sa.Text(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "supplier_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("supplier_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("running_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "budget_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("graph_version", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint(
            "period_type IN ('monthly', 'weekly')",
            name="ck_research_batches_period_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_research_batches_status",
        ),
        sa.UniqueConstraint(
            "owner_user_id", "period_type", "period_key",
            name="uq_research_batches_owner_period",
        ),
    )
    op.create_index(
        "ix_research_batches_status_period",
        "research_batches",
        ["status", "period_type", "period_key"],
    )
    op.add_column(
        "research_tasks",
        sa.Column(
            "batch_id",
            sa.BigInteger(),
            sa.ForeignKey("research_batches.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_constraint("ck_research_tasks_task_type", "research_tasks", type_="check")
    op.create_check_constraint(
        "ck_research_tasks_task_type",
        "research_tasks",
        "task_type IN ('manual', 'daily', 'weekly', 'monthly')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_research_tasks_task_type", "research_tasks", type_="check")
    op.create_check_constraint(
        "ck_research_tasks_task_type",
        "research_tasks",
        "task_type IN ('manual', 'daily', 'weekly')",
    )
    op.drop_column("research_tasks", "batch_id")
    op.drop_index("ix_research_batches_status_period", table_name="research_batches")
    op.drop_table("research_batches")
