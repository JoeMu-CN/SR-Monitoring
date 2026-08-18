"""增加周期批次容量阻塞状态。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_batches",
        sa.Column("topic", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.drop_constraint("ck_research_batches_status", "research_batches", type_="check")
    op.create_check_constraint(
        "ck_research_batches_status",
        "research_batches",
        "status IN ('queued', 'running', 'succeeded', 'partial', 'failed', "
        "'cancelled', 'capacity_blocked')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE research_batches "
            "SET status = 'failed', error = COALESCE(error, 'capacity_blocked 状态回滚') "
            "WHERE status = 'capacity_blocked'"
        )
    )
    op.drop_constraint("ck_research_batches_status", "research_batches", type_="check")
    op.create_check_constraint(
        "ck_research_batches_status",
        "research_batches",
        "status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
    )
    op.drop_column("research_batches", "topic")
