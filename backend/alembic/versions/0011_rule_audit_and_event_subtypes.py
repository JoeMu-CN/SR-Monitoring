"""事件细类与规则版本提醒修订。

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("risk_events", sa.Column("event_subtype", sa.Text(), nullable=True))
    op.drop_constraint("uq_risk_alerts_match_id", "risk_alerts", type_="unique")
    op.create_index(
        "uq_risk_alerts_current_match_id",
        "risk_alerts",
        ["match_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )


def downgrade() -> None:
    op.drop_index("uq_risk_alerts_current_match_id", table_name="risk_alerts")
    op.execute(
        """
        DELETE FROM risk_alerts older
        USING risk_alerts newer
        WHERE older.match_id = newer.match_id
          AND (older.updated_at, older.id) < (newer.updated_at, newer.id)
        """
    )
    op.create_unique_constraint("uq_risk_alerts_match_id", "risk_alerts", ["match_id"])
    op.drop_column("risk_events", "event_subtype")
