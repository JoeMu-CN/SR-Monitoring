"""启用 PostGIS 扩展。

Revision ID: 0001
Revises:
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # 不自动删除扩展，避免未来存在地理数据时造成数据损坏。
    pass
