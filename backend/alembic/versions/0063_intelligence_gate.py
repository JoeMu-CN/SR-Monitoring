"""情报门控：新增 skipped 终态与判定依据列。

Revision ID: 0063
Revises: 0062

背景：
- 情报生成改为两段式（门控 → 报告）：门控判定「这条公告是否值得进入情报页」，
  不相关的事件不再发起报告调用，以 ``status='skipped'`` 落库并在列表隐藏。
- 旧约束只允许 ready / failed，无法表达「已判定不相关」，因此需要扩展约束。
- ``gate_reason`` 存判定依据（一句话中文），``gate_prompt_version`` 存门控口径版本：
  两者分开记录，才能区分「报告口径」与「筛选口径」各自是否需要重跑；重判脚本以
  ``gate_prompt_version`` 与当前 ``GATE_PROMPT_VERSION`` 的差异挑选待重判行。

upgrade 语义：
- 只加两列（nullable，历史行保持 NULL）+ 替换 status 约束；不改写任何既有行。
- 既有 157 条 ready 报告与 failed 行语义不变，历史数据不做回填（是否重判由运营用
  脚本显式决定，迁移不代替运营做筛选决策）。

downgrade 语义：
- 删除 ``status='skipped'`` 的行：旧约束无法表达该状态，保留会导致约束创建失败。
  这些行只承载「已判定不相关 + 理由」，不含报告正文，删除不损失可交付内容；其余
  ready / failed 行原样保留。
- 随后恢复旧约束并删除两列；``gate_reason``/``gate_prompt_version`` 在 ready 行上
  本就为 NULL（门控通过不记录理由），因此删列不丢失报告内容。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CONSTRAINT = "ck_intelligence_reports_status"
NEW_STATUS_SQL = "status IN ('ready', 'failed', 'skipped')"
OLD_STATUS_SQL = "status IN ('ready', 'failed')"


def upgrade() -> None:
    op.add_column(
        "intelligence_reports", sa.Column("gate_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "intelligence_reports",
        sa.Column("gate_prompt_version", sa.Text(), nullable=True),
    )
    op.drop_constraint(
        STATUS_CONSTRAINT, "intelligence_reports", type_="check"
    )
    op.create_check_constraint(
        STATUS_CONSTRAINT, "intelligence_reports", NEW_STATUS_SQL
    )


def downgrade() -> None:
    # 先清掉旧约束无法表达的行，否则恢复旧约束会失败；这些行不含报告正文。
    op.execute("DELETE FROM intelligence_reports WHERE status = 'skipped'")
    op.drop_constraint(
        STATUS_CONSTRAINT, "intelligence_reports", type_="check"
    )
    op.create_check_constraint(
        STATUS_CONSTRAINT, "intelligence_reports", OLD_STATUS_SQL
    )
    op.drop_column("intelligence_reports", "gate_prompt_version")
    op.drop_column("intelligence_reports", "gate_reason")
