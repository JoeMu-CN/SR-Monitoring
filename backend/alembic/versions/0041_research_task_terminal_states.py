"""补充周期子任务的跳过与预算耗尽终态。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_batches",
        sa.Column(
            "budget_exhausted_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.drop_constraint("ck_research_tasks_status", "research_tasks", type_="check")
    op.create_check_constraint(
        "ck_research_tasks_status",
        "research_tasks",
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', "
        "'skipped', 'budget_exhausted')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE research_tasks "
            "SET status = 'failed', error = COALESCE(error, '终态回滚') "
            "WHERE status IN ('skipped', 'budget_exhausted')"
        )
    )
    op.drop_constraint("ck_research_tasks_status", "research_tasks", type_="check")
    op.create_check_constraint(
        "ck_research_tasks_status",
        "research_tasks",
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    )
    op.drop_column("research_batches", "budget_exhausted_count")
