"""合并研究轨与新增监控轨地址字段迁移。

Revision ID: 0034
Revises: 0032, 0033
Create Date: 2026-08-16
"""

from collections.abc import Sequence

revision: str = "0034"
down_revision: tuple[str, str] = ("0032", "0033")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
