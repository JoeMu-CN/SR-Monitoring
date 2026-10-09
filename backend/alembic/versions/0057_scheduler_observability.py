"""Scheduler 排期快照与每次调度执行历史表。

Revision ID: 0057
Revises: 0056

背景：
- 只读监控健康聚合需要展示真实排期（由 Scheduler 进程从 ``Job.next_run_time``
  写库，Web 不推算）与每次调度执行的最新 30 条历史。
- ``scheduler_job_registry``：每个非内部维护 job 一行（job_id/name/next_run_at）。
- ``scheduler_job_run``：每次调度一行；事件幂等键 ``(job_id, scheduled_run_at)``
  由唯一约束兜底并发。``status`` 仅允许
  ``running/completed/error/missed/max_instances`` —— ``completed`` 只表示
  callable 正常返回，绝不命名为 ``succeeded``。
- 两表均不保存 exception/traceback/retval、信号标题正文、凭据或其他敏感数据，
  仅保存稳定状态与 UTC 时间戳。

upgrade 语义：
- 创建两张空表；既有调度语义、触发器、misfire/coalesce/max_instances 与业务
  数据不受影响。

downgrade 语义：
- 删除索引后按依赖顺序删表；其余表与数据不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_RUN_STATUS_VALUES = "'running', 'completed', 'error', 'missed', 'max_instances'"


def upgrade() -> None:
    op.create_table(
        "scheduler_job_registry",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "scheduler_job_run",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("scheduled_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({JOB_RUN_STATUS_VALUES})",
            name="ck_scheduler_job_run_status",
        ),
        sa.UniqueConstraint(
            "job_id", "scheduled_run_at", name="uq_scheduler_job_run_job_scheduled"
        ),
    )
    op.create_index(
        "ix_scheduler_job_run_scheduled_run_at",
        "scheduler_job_run",
        ["scheduled_run_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduler_job_run_scheduled_run_at", table_name="scheduler_job_run"
    )
    op.drop_table("scheduler_job_run")
    op.drop_table("scheduler_job_registry")
