"""隔离风险查询与数据源接入 Agent 会话。

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column(
            "agent_kind",
            sa.Text(),
            server_default=sa.text("'risk_query'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_agent_sessions_agent_kind",
        "agent_sessions",
        "agent_kind IN ('risk_query', 'source_onboarding')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_sessions_agent_kind", "agent_sessions", type_="check"
    )
    op.drop_column("agent_sessions", "agent_kind")
