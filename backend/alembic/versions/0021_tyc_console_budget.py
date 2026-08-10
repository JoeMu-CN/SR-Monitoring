"""天眼查额度迁移到数据源控制台配置。

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET login_config = jsonb_set(
                jsonb_set(
                    login_config,
                    '{daily_limit}',
                    COALESCE(login_config->'daily_limit', '80'::jsonb),
                    true
                ),
                '{monthly_limit}',
                COALESCE(login_config->'monthly_limit', '900'::jsonb),
                true
            )
            WHERE code = 'tianyancha'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET login_config = login_config - 'daily_limit' - 'monthly_limit'
            WHERE code = 'tianyancha'
            """
        )
    )
