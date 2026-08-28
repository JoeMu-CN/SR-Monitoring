"""数据源信源级信号有效期（signal_validity_days）。

Revision ID: 0046
Revises: 0045
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("signal_validity_days", sa.Integer(), nullable=True),
    )
    # 语义：NULL=永久有效（政策/制裁/监管类默认）；正整数=信号自发生起 N 天内有效。
    op.create_check_constraint(
        "ck_data_sources_signal_validity_days",
        "data_sources",
        "signal_validity_days IS NULL OR signal_validity_days >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_data_sources_signal_validity_days", "data_sources", type_="check"
    )
    op.drop_column("data_sources", "signal_validity_days")
