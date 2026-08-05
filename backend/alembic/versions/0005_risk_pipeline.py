"""新增 D5 风险事件、关联和提醒。

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name="ck_risk_events_severity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_risk_events_dedup_key"),
    )
    op.create_table(
        "risk_event_signals",
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signal_id"], ["raw_signals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "signal_id"),
    )
    op.create_table(
        "supplier_event_matches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_supplier_event_matches_score"),
        sa.ForeignKeyConstraint(["event_id"], ["risk_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supplier_id", "event_id", name="uq_supplier_event_matches"),
    )
    op.create_table(
        "risk_alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("score_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("level IN ('P1', 'P2', 'P3', 'P4')", name="ck_risk_alerts_level"),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_risk_alerts_score"),
        sa.CheckConstraint("status IN ('current', 'expired')", name="ck_risk_alerts_status"),
        sa.ForeignKeyConstraint(["match_id"], ["supplier_event_matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", name="uq_risk_alerts_match_id"),
    )
    op.create_index(
        "ix_risk_alerts_status_updated",
        "risk_alerts",
        ["status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_risk_alerts_status_updated", table_name="risk_alerts")
    op.drop_table("risk_alerts")
    op.drop_table("supplier_event_matches")
    op.drop_table("risk_event_signals")
    op.drop_table("risk_events")
