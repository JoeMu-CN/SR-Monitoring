"""迁移更新天眼查说明栏文案。

Revision ID: 0051
Revises: 0050
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET description = '外部核查工具：运行密钥在信息源控制台配置并加密存库；启用后可手动批量核查全部启用供应商，并于每天北京时间 06:00 自动批量核查；不参与通用 cron 拉取式采集。'
            WHERE code = 'tianyancha'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET description = '按需企业工商核查工具，不参与定时采集；运行密钥在信息源控制台统一配置与启停。'
            WHERE code = 'tianyancha'
            """
        )
    )
