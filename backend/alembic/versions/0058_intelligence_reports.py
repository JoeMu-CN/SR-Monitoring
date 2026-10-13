"""情报聚合报告表 intelligence_reports。

Revision ID: 0058
Revises: 0057

背景：
- 「情报聚合」页面展示政策/行业维度事件的 LLM 简要报告与风险提醒建议，与
  风险提醒（risk_alerts）解耦：是否命中供应商、是否生成提醒都不影响它。
- 一行 = 一个 risk_event 在一个情报分类（policy/industry）下的一份报告。
  event_id 外键 ON DELETE CASCADE，事件被保留清理删除时报告一并消失。

唯一性：
- UNIQUE(event_id, category) 保证同一事件在同一分类下只生成一次；调度批量
  生成与页面查询都以它为幂等依据（并发下第二次写入由数据库拒绝）。
- 不使用单列 UNIQUE(event_id)：分类由信源归属推导，正常情况二者等价，保留
  category 维度可在维度声明调整后允许同一事件在新分类下重新生成。

约束：
- status 仅允许 ready/failed；failed 行记录 error，用于诊断与冷却后重试。
- suggestions 固定为 JSONB 字符串数组；report_zh 仅在 status='ready' 时非空。

upgrade 语义：创建空表，既有调度语义与业务数据不受影响。
downgrade 语义：直接删表（其数据完全由本表承载，无外部引用）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intelligence_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("risk_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("report_zh", sa.Text(), nullable=True),
        sa.Column(
            "suggestions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "event_id", "category", name="uq_intelligence_reports_event"
        ),
        sa.CheckConstraint(
            "category IN ('policy', 'industry')",
            name="ck_intelligence_reports_category",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'failed')", name="ck_intelligence_reports_status"
        ),
    )
    op.create_index(
        "ix_intelligence_reports_category_generated",
        "intelligence_reports",
        ["category", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_intelligence_reports_category_generated", table_name="intelligence_reports"
    )
    op.drop_table("intelligence_reports")