"""信号消费优先级：raw_signals.priority + 队列排序索引。

Revision ID: 0065
Revises: 0064

背景：消费队列按确定性预筛后的优先级排序，让「供应商命中 / 高影响 / 临近失效」
的信号先被分析，避免清单类高频全量信源把队列堵死。

upgrade 语义：
1. 新增 ``raw_signals.priority``（SmallInteger，NOT NULL，默认 50）。
2. 建 ``(priority DESC, collected_at)`` 索引，支撑队列按优先级 + 先进先出取信号。
3. 按来源回填历史行：清单类信源 10、高频结构化源 20、政策/监管类 70、其余 50。
   分档口径与 ``app.signals.relevance._default_source_tiers`` 一致；清单 code 在
   迁移内联，避免迁移依赖应用代码。

downgrade 语义：删除索引与列，priority 打分随列一起消失（无其他表引用）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 清单类信源 code（与 app.signals.relevance._LIST_SOURCE_CODES 保持一致）。
_LIST_SOURCE_CODES: tuple[str, ...] = (
    "ofac-sdn",
    "bis-entity-list",
    "uflpa-entity-list",
    "mofcom-entity-detail",
    "mofcom-entity-control",
)


def upgrade() -> None:
    op.add_column(
        "raw_signals",
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("50"),
        ),
    )
    op.create_index(
        "ix_raw_signals_priority_collected",
        "raw_signals",
        [sa.text("priority DESC"), "collected_at"],
    )
    codes = ", ".join(f"'{code}'" for code in _LIST_SOURCE_CODES)
    op.execute(
        f"""
        UPDATE raw_signals rs
        SET priority = CASE
            WHEN ds.code IN ({codes}) THEN 10
            WHEN ds.source_type IN ('weather', 'official_api', 'market_data') THEN 20
            WHEN ds.source_type IN
                ('policy', 'regulatory', 'incident', 'geopolitical') THEN 70
            ELSE 50
        END
        FROM data_sources ds
        WHERE ds.id = rs.source_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_raw_signals_priority_collected", table_name="raw_signals")
    op.drop_column("raw_signals", "priority")
