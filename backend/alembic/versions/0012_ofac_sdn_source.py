"""注册 OFAC SDN 官方公开制裁名单数据源。

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
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
                "code": "ofac-sdn",
                "name": "OFAC SDN 制裁名单",
                "source_type": "sanctions",
                "credibility": 95,
                "schedule": "0 */6 * * *",
                "enabled": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_sources WHERE code = 'ofac-sdn'")
