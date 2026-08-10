"""数据源控制台运行密钥加密列，并迁移天眼查为控制台配置。

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources", sa.Column("api_key_encrypted", sa.Text(), nullable=True)
    )
    # 天眼查运行密钥改为控制台统一配置：credential_ref 置空，
    # login_config 标记密钥来源为控制台；API Key 需管理员在控制台重新配置一次。
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET login_config = '{"mode":"on_demand","secret_source":"console"}'::jsonb,
                credential_ref = NULL,
                description = '按需企业工商核查工具，不参与定时采集；运行密钥在数据源控制台统一配置与启停。'
            WHERE code = 'tianyancha'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE data_sources
            SET login_config = '{"mode":"on_demand","secret_source":"environment"}'::jsonb,
                credential_ref = 'env:TYC_API_KEY',
                description = '按需企业工商核查工具，不参与定时采集；运行密钥仅通过环境变量注入。'
            WHERE code = 'tianyancha'
            """
        )
    )
    op.drop_column("data_sources", "api_key_encrypted")
