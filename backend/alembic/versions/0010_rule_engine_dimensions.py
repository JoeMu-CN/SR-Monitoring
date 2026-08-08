"""规则引擎维度化：规则配置表 + 供应商行业/原材料字段。

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08

- 新增 rule_dimension_configs：可视化工作台编辑的维度启停与参数覆盖，
  引擎每次处理事件时合并，实现热更新。
- suppliers 增加 industry（行业标签）与 raw_materials（关键原材料），
  作为宏观维度（国家/行业匹配柱）的供应商侧抓手。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("industry", sa.Text(), nullable=True))
    op.add_column(
        "suppliers",
        sa.Column(
            "raw_materials",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "rule_dimension_configs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_rule_dimension_configs_key"),
    )


def downgrade() -> None:
    op.drop_table("rule_dimension_configs")
    op.drop_column("suppliers", "raw_materials")
    op.drop_column("suppliers", "industry")
