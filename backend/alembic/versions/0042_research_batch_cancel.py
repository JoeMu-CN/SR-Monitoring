"""增加周期批次取消请求与预算耗尽终态。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_batches",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_research_batches_status", "research_batches", type_="check")
    op.create_check_constraint(
        "ck_research_batches_status",
        "research_batches",
        "status IN ('queued', 'running', 'succeeded', 'partial', 'failed', "
        "'cancelled', 'capacity_blocked', 'budget_exhausted')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE research_batches "
            "SET status = 'failed', error = COALESCE(error, '预算耗尽状态回滚') "
            "WHERE status = 'budget_exhausted'"
        )
    )
    op.drop_constraint("ck_research_batches_status", "research_batches", type_="check")
    op.create_check_constraint(
        "ck_research_batches_status",
        "research_batches",
        "status IN ('queued', 'running', 'succeeded', 'partial', 'failed', "
        "'cancelled', 'capacity_blocked')",
    )
    op.drop_column("research_batches", "cancel_requested_at")
