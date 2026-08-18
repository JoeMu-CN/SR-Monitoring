"""新增周期研究运行时配置与周报批准记录。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_schedule_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("schedule_type", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "cron_expression",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'30 8 * * mon'"),
        ),
        sa.Column("topic_template", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "budget_template",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("approved_monthly_quota", sa.Integer(), nullable=True),
        sa.Column(
            "approved_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column(
            "updated_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "schedule_type IN ('weekly', 'monthly')",
            name="ck_research_schedule_configs_type",
        ),
        sa.CheckConstraint(
            "approved_monthly_quota IS NULL OR approved_monthly_quota > 0",
            name="ck_research_schedule_configs_quota",
        ),
        sa.UniqueConstraint("schedule_type", name="uq_research_schedule_configs_type"),
    )


def downgrade() -> None:
    op.drop_table("research_schedule_configs")
