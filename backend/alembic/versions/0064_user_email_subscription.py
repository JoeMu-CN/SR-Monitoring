"""用户邮件推送：三张新表 + 投递表按用户扩展。

Revision ID: 0064
Revises: 0063

背景（《风险提醒邮件推送接入方案.md》第 5 章）：
- 推送方案与供应商归属按用户维护 → ``user_notification_subscriptions`` /
  ``user_supplier_assignments``，矩阵弹窗直接读写，改库即生效（热更新）。
- 月报需要按 (user_id, period) 幂等并保留审计 → ``notification_monthly_reports``。
- 现有投递表唯一键 ``(alert_id, channel)`` 无法容纳「同一提醒 × N 个用户」，
  必须扩键：``user_id`` 为空时仍是全局渠道记录，非空时按用户去重。

upgrade 语义：
1. 建三张新表（默认值全部关闭，升级后不会突然群发）。
2. ``notification_deliveries`` 增加 ``user_id``（nullable + 外键 CASCADE）。
3. 删除旧唯一约束，改为两个部分唯一索引（Postgres 唯一索引不对 NULL 去重，
   不能简单改成三列联合唯一键）。
4. 新增 ``(user_id, created_at)`` 索引，支撑按用户限频与历史查询。

downgrade 语义：
- 严格反向：删索引 → 恢复旧唯一约束 → 删外键与列 → 删三张新表。
- 注意：若库内已存在 ``user_id IS NOT NULL`` 的邮件投递行，同一 ``(alert_id,
  channel)`` 可能有重复，恢复旧唯一约束会失败；此时需先清理这些行（本迁移不代替
  运营删除审计数据，故不隐式 DELETE）。批次 1 只建结构与通道，尚无此类行。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_notification_subscriptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "push_p1", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "push_p2", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "push_p3", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "push_monthly_report",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", name="uq_user_notification_subscriptions_user"
        ),
    )

    op.create_table(
        "user_supplier_assignments",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "supplier_id",
            sa.BigInteger(),
            sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "supplier_id"),
    )
    op.create_index(
        "ix_user_supplier_assignments_supplier",
        "user_supplier_assignments",
        ["supplier_id"],
    )

    op.create_table(
        "notification_monthly_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('success','failed','skipped_no_data')",
            name="ck_notification_monthly_reports_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "period", name="uq_notification_monthly_reports_user_period"
        ),
    )
    op.create_index(
        "ix_notification_monthly_reports_period_status",
        "notification_monthly_reports",
        ["period", "status"],
    )

    op.add_column(
        "notification_deliveries",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_deliveries_user_id",
        "notification_deliveries",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_notification_alert_channel", "notification_deliveries", type_="unique"
    )
    op.create_index(
        "uq_notification_alert_channel_global",
        "notification_deliveries",
        ["alert_id", "channel"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_notification_alert_channel_user",
        "notification_deliveries",
        ["alert_id", "channel", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_notification_deliveries_user_created",
        "notification_deliveries",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_user_created",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "uq_notification_alert_channel_user",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "uq_notification_alert_channel_global",
        table_name="notification_deliveries",
    )
    op.create_unique_constraint(
        "uq_notification_alert_channel",
        "notification_deliveries",
        ["alert_id", "channel"],
    )
    op.drop_constraint(
        "fk_notification_deliveries_user_id",
        "notification_deliveries",
        type_="foreignkey",
    )
    op.drop_column("notification_deliveries", "user_id")

    op.drop_index(
        "ix_notification_monthly_reports_period_status",
        table_name="notification_monthly_reports",
    )
    op.drop_table("notification_monthly_reports")
    op.drop_index(
        "ix_user_supplier_assignments_supplier",
        table_name="user_supplier_assignments",
    )
    op.drop_table("user_supplier_assignments")
    op.drop_table("user_notification_subscriptions")
