"""Scheduler 运行时观测状态表。

Revision ID: 0049
Revises: 0048
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_VALUES = "'idle', 'running', 'succeeded', 'failed'"
ERROR_CODE_PATTERN = "^[a-z][a-z0-9_]{0,63}$"


def upgrade() -> None:
    op.create_table(
        "scheduler_runtime_state",
        sa.Column("job_key", sa.Text(), primary_key=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'idle'"),
            nullable=False,
        ),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "processed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "filtered_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({STATUS_VALUES})",
            name="ck_scheduler_runtime_state_status",
        ),
        sa.CheckConstraint(
            "processed_count >= 0 AND filtered_count >= 0 AND failed_count >= 0",
            name="ck_scheduler_runtime_state_counts_non_negative",
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code ~ '{ERROR_CODE_PATTERN}'",
            name="ck_scheduler_runtime_state_error_code_stable",
        ),
    )


def downgrade() -> None:
    op.drop_table("scheduler_runtime_state")
