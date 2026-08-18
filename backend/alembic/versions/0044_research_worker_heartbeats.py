"""记录研究 Worker 的最新运行心跳。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_worker_heartbeats",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("orchestrator", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'online'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('online', 'stopped')",
            name="ck_research_worker_heartbeats_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_id", name="uq_research_worker_heartbeats_worker"),
    )
    op.create_index(
        "ix_research_worker_heartbeats_last_seen",
        "research_worker_heartbeats",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_worker_heartbeats_last_seen",
        table_name="research_worker_heartbeats",
    )
    op.drop_table("research_worker_heartbeats")
