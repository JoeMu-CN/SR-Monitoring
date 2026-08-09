"""增加声明式数据源适配器配置。

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column(
            "adapter_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "data_sources",
        sa.Column("adapter_status", sa.Text(), nullable=False, server_default="unconfigured"),
    )
    op.add_column(
        "data_sources",
        sa.Column("adapter_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "data_sources",
        sa.Column("adapter_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_data_sources_adapter_status",
        "data_sources",
        "adapter_status IN ('builtin','unconfigured','draft','published','invalid')",
    )
    op.execute(
        "UPDATE data_sources SET adapter_status = 'builtin' "
        "WHERE code IN ('manual-json','nmc-weather','ofac-sdn')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_data_sources_adapter_status", "data_sources", type_="check")
    op.drop_column("data_sources", "adapter_published_at")
    op.drop_column("data_sources", "adapter_version")
    op.drop_column("data_sources", "adapter_status")
    op.drop_column("data_sources", "adapter_config")
