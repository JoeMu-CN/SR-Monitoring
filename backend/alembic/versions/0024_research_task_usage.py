"""研究任务预算快照与执行计量。

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column("budget_snapshot", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "research_tasks",
        sa.Column("search_queries_used", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_tasks",
        sa.Column("search_results_used", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_tasks",
        sa.Column("input_tokens_used", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_tasks",
        sa.Column("output_tokens_used", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_tasks",
        sa.Column(
            "cost_amount",
            sa.Numeric(18, 8),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("research_tasks", sa.Column("current_step", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_research_tasks_usage_nonnegative",
        "research_tasks",
        "search_queries_used >= 0 AND search_results_used >= 0 "
        "AND input_tokens_used >= 0 AND output_tokens_used >= 0 AND cost_amount >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_research_tasks_usage_nonnegative", "research_tasks", type_="check")
    op.drop_column("research_tasks", "current_step")
    op.drop_column("research_tasks", "cost_amount")
    op.drop_column("research_tasks", "output_tokens_used")
    op.drop_column("research_tasks", "input_tokens_used")
    op.drop_column("research_tasks", "search_results_used")
    op.drop_column("research_tasks", "search_queries_used")
    op.drop_column("research_tasks", "budget_snapshot")
