"""通知基础：订阅配置与投递记录。

Revision ID: 0045
Revises: 0044
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_subscriptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("receiver", sa.Text(), nullable=True),
        sa.Column(
            "push_levels",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'[\"P1\",\"P2\"]'::jsonb"),
            nullable=False,
        ),
        sa.Column("quiet_hours", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel"),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "alert_id",
            sa.BigInteger(),
            sa.ForeignKey("risk_alerts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("pushed_level", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('success','failed','queued','merged','rate_limited','quiet_suppressed')",
            name="ck_notification_deliveries_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "channel", name="uq_notification_alert_channel"),
    )
    op.create_index(
        "ix_notification_deliveries_channel_created",
        "notification_deliveries",
        ["channel", "created_at"],
    )
    op.create_index(
        "ix_notification_deliveries_status_created",
        "notification_deliveries",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_status_created",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_channel_created",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
    op.drop_table("notification_subscriptions")
