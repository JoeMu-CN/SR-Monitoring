"""新增天眼查调用计费记录表。

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tyc_usage_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "called_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tyc_usage_records_called_at",
        "tyc_usage_records",
        ["called_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tyc_usage_records_called_at", table_name="tyc_usage_records")
    op.drop_table("tyc_usage_records")
