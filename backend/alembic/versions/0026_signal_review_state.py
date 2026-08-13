"""为信号分析增加防漏报复核状态。

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("ai_analysis_records")}
    if "needs_review" not in columns:
        op.add_column(
            "ai_analysis_records",
            sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    if "review_reason" not in columns:
        op.add_column("ai_analysis_records", sa.Column("review_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("ai_analysis_records")}
    if "review_reason" in columns:
        op.drop_column("ai_analysis_records", "review_reason")
    if "needs_review" in columns:
        op.drop_column("ai_analysis_records", "needs_review")
