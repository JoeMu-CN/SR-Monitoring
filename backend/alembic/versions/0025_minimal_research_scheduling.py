"""即时信源高频采集与研究任务最小调度默认值。

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET schedule = '*/5 * * * *'
            WHERE code = 'nmc-weather' AND schedule = '*/30 * * * *'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET schedule = '*/30 * * * *'
            WHERE code = 'nmc-weather' AND schedule = '*/5 * * * *'
            """
        )
    )
