"""新增研究 Provider 自然月额度账本。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_provider_quota_periods",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column("monthly_limit", sa.Integer(), nullable=False),
        sa.Column(
            "scheduled_reserved", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "manual_reserved", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "monthly_limit > 0 AND scheduled_reserved >= 0 "
            "AND manual_reserved >= 0 AND used >= 0",
            name="ck_research_provider_quota_nonnegative",
        ),
        sa.UniqueConstraint(
            "provider", "period_key", name="uq_research_provider_quota_period"
        ),
    )
    op.create_index(
        "ix_research_provider_quota_period",
        "research_provider_quota_periods",
        ["provider", "period_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_provider_quota_period",
        table_name="research_provider_quota_periods",
    )
    op.drop_table("research_provider_quota_periods")
