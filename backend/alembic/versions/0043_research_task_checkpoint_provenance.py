"""记录研究任务的 LangGraph 版本与 checkpoint 线程键。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("research_tasks", sa.Column("graph_version", sa.Text(), nullable=True))
    op.add_column(
        "research_tasks",
        sa.Column("checkpoint_thread_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_tasks", "checkpoint_thread_id")
    op.drop_column("research_tasks", "graph_version")
