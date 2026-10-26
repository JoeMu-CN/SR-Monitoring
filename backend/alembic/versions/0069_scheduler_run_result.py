"""Scheduler 执行历史保存脱敏结果摘要。

Revision ID: 0069
Revises: 0068

背景：
- 底部节点详情需要按 job 查询最近若干次执行，并展示每次实际处理/产出的结构化
  摘要；``scheduler_job_run`` 原有列只有状态与时间戳。
- 只保存经 ``RunResult`` 校验的受限摘要（summary + 计数 metrics/outputs），
  绝不保存原始 retval、exception、traceback、日志正文、凭据或路径。

upgrade 语义：
- 新增可空 ``result_summary`` JSONB 列；旧行保持 NULL，不编造历史结果。
- 新增 ``ix_scheduler_job_run_job_id`` 支持按任务查询最近执行。
- 既有调度语义、状态约束与唯一幂等键不变。

downgrade 语义：
- 删除索引与列；其余调度历史行与数据不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduler_job_run",
        sa.Column(
            "result_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_scheduler_job_run_job_id",
        "scheduler_job_run",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduler_job_run_job_id", table_name="scheduler_job_run")
    op.drop_column("scheduler_job_run", "result_summary")
