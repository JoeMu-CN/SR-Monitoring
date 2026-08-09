"""将天眼查登记为统一数据源目录中的按需外部核查工具。

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO data_sources (
                code, name, source_type, credibility, schedule, endpoint_url,
                auth_type, login_config, credential_ref, description, enabled
            ) VALUES (
                'tianyancha', '天眼查企业核查', 'external_tool', 90, NULL,
                'https://mcp.tianyancha.com/v1', 'api_key',
                '{"mode":"on_demand","secret_source":"environment"}'::jsonb,
                'env:TYC_API_KEY',
                '按需企业工商核查工具，不参与定时采集；运行密钥仅通过环境变量注入。',
                false
            )
            ON CONFLICT (code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_sources WHERE code = 'tianyancha'")
