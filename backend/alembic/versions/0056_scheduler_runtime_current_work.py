"""scheduler_runtime_state 增加当前工作观测列（current_kind/current_stage/current_item_id）。

Revision ID: 0056
Revises: 0055

背景：
- 只读监控健康聚合需要展示调度器“当前正在采集哪个信源、处理哪个信号”。
- 三个新列全部可空：旧行与未登记任务保持 NULL，不改变既有语义。
- 枚举受 CheckConstraint 约束，与 ``app.scheduler.runtime_models`` 的
  ``CURRENT_WORK_KIND_VALUES`` / ``CURRENT_WORK_STAGE_VALUES`` 保持一致；
  ``current_item_id`` 只允许正数（信号 id）。

upgrade 语义：
- 新增 ``current_kind`` / ``current_stage`` / ``current_item_id`` 三列（可空）。
- 升级时停留在 ``running`` 的旧行 current_* 均为 NULL：健康接口按“未登记”
  处理并省略，不会把缺少工作类型的旧行伪报为当前工作。

downgrade 语义：
- 先删除三条 CheckConstraint，再删除三列；其余列与数据不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_WORK_KIND_VALUES = "'source_collection', 'pending_signal_processing'"
CURRENT_WORK_STAGE_VALUES = "'collecting', 'processing_signal'"


def upgrade() -> None:
    op.add_column(
        "scheduler_runtime_state",
        sa.Column("current_kind", sa.Text(), nullable=True),
    )
    op.add_column(
        "scheduler_runtime_state",
        sa.Column("current_stage", sa.Text(), nullable=True),
    )
    op.add_column(
        "scheduler_runtime_state",
        sa.Column("current_item_id", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_scheduler_runtime_state_current_kind",
        "scheduler_runtime_state",
        f"current_kind IS NULL OR current_kind IN ({CURRENT_WORK_KIND_VALUES})",
    )
    op.create_check_constraint(
        "ck_scheduler_runtime_state_current_stage",
        "scheduler_runtime_state",
        f"current_stage IS NULL OR current_stage IN ({CURRENT_WORK_STAGE_VALUES})",
    )
    op.create_check_constraint(
        "ck_scheduler_runtime_state_current_item_positive",
        "scheduler_runtime_state",
        "current_item_id IS NULL OR current_item_id > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_scheduler_runtime_state_current_item_positive",
        "scheduler_runtime_state",
        type_="check",
    )
    op.drop_constraint(
        "ck_scheduler_runtime_state_current_stage",
        "scheduler_runtime_state",
        type_="check",
    )
    op.drop_constraint(
        "ck_scheduler_runtime_state_current_kind",
        "scheduler_runtime_state",
        type_="check",
    )
    op.drop_column("scheduler_runtime_state", "current_item_id")
    op.drop_column("scheduler_runtime_state", "current_stage")
    op.drop_column("scheduler_runtime_state", "current_kind")
