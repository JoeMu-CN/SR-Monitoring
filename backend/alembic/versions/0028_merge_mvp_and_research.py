"""合并监控轨 MVP 与研究轨迁移分支。

Revision ID: 0028
Revises: 0026, 0027
"""

from collections.abc import Sequence

revision: str = "0028"
down_revision: tuple[str, str] = ("0026", "0027")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 纯合并节点；两个分支的表结构互不冲突。
    pass


def downgrade() -> None:
    # 回退到合并前的两个分支头，不删除任何业务表。
    pass
