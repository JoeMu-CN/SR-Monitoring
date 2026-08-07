"""注册中央气象台天气预警数据源。

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.bulk_insert(
        sa.table(
            "data_sources",
            sa.column("code", sa.Text()),
            sa.column("name", sa.Text()),
            sa.column("source_type", sa.Text()),
            sa.column("credibility", sa.SmallInteger()),
            sa.column("schedule", sa.Text()),
            sa.column("enabled", sa.Boolean()),
        ),
        [
            {
                "code": "nmc-weather",
                "name": "中央气象台天气预警",
                "source_type": "weather",
                "credibility": 80,
                "schedule": "*/30 * * * *",
                "enabled": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_sources WHERE code = 'nmc-weather'")
