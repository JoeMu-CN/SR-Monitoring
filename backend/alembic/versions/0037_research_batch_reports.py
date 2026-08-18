"""支持月报批次组合报告。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_reports",
        sa.Column(
            "batch_id",
            sa.BigInteger(),
            sa.ForeignKey("research_batches.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.alter_column("research_reports", "task_id", nullable=True)
    op.create_check_constraint(
        "ck_research_reports_task_or_batch",
        "research_reports",
        "(task_id IS NULL) <> (batch_id IS NULL)",
    )
    op.create_index(
        "ix_research_reports_batch_created",
        "research_reports",
        ["batch_id", "created_at"],
    )
    op.create_index(
        "uq_research_reports_batch_id",
        "research_reports",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_research_reports_batch_id", table_name="research_reports")
    op.drop_index("ix_research_reports_batch_created", table_name="research_reports")
    op.drop_constraint(
        "ck_research_reports_task_or_batch", "research_reports", type_="check"
    )
    op.alter_column("research_reports", "task_id", nullable=False)
    op.drop_column("research_reports", "batch_id")
