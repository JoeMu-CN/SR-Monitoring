"""监控轨 MVP 的信号待复核状态。

Revision ID: 0027
Revises: 0021

该迁移故意直接从监控轨的 0021 分支开始，避免首次 ECS MVP 部署创建研究轨表。
研究轨分支通过 0028 合并回默认 head，不改变既有 0022~0026 文件。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = ("mvp",)
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
    # 完整 head 已由 0026 提供同样字段；回退合并分支时不能破坏研究轨状态。
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("research_tasks"):
        return
    columns = {column["name"] for column in inspector.get_columns("ai_analysis_records")}
    if "review_reason" in columns:
        op.drop_column("ai_analysis_records", "review_reason")
    if "needs_review" in columns:
        op.drop_column("ai_analysis_records", "needs_review")
