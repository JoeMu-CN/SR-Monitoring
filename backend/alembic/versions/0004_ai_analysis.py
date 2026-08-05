"""新增 AI 分析记录。

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_ai_analysis_records_status",
        ),
        sa.ForeignKeyConstraint(["signal_id"], ["raw_signals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_analysis_records_signal_started",
        "ai_analysis_records",
        ["signal_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_analysis_records_signal_started", table_name="ai_analysis_records"
    )
    op.drop_table("ai_analysis_records")
