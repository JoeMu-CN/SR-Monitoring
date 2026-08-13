"""研究报告草稿表。

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("research_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("draft_payload", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("review_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'rejected')",
            name="ck_research_reports_status",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_research_reports_review_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_reports_task_created",
        "research_reports",
        ["task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_reports_task_created", table_name="research_reports")
    op.drop_table("research_reports")
