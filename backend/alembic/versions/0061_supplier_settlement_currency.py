"""为供应商新增可空的结算币种 settlement_currency（ISO 4217 三字母大写）。

Revision ID: 0061
Revises: 0060

背景：
- 市场指标监控（汇率波动）需要知道供应商与我方的结算币种，否则 USD/JPY/CNY 敞口
  无法与汇率信号对齐。该字段只描述「结算口径」，不参与任何既有匹配与评分规则。

方案：
- 新增可空 ``TEXT`` 列。历史行一律保持 NULL：不回填任何猜测值，避免把未知的结算
  口径写成看起来可信的错误数据；可空保证既有写入路径（创建/更新/导入）不改也不会失败。
- 落库 CHECK ``settlement_currency ~ '^[A-Z]{3}$'`` 与既有
  ``ck_suppliers_country_code`` 同构：只有三字母大写 ISO 4217 代码可入库。
  大小写归一在 Schema 层（``app.suppliers.schemas``）完成，CHECK 只做最后一道闸。

upgrade 语义：
1. ``ALTER TABLE suppliers ADD COLUMN settlement_currency TEXT NULL``；
2. ``ALTER TABLE suppliers ADD CONSTRAINT ck_suppliers_settlement_currency
   CHECK (settlement_currency IS NULL OR settlement_currency ~ '^[A-Z]{3}$')``。
   显式写出 IS NULL 分支而不是依赖 CHECK 对 NULL 的三值逻辑，保证语义可读且与
   ``ck_supplier_sites_latitude`` 等既有约束风格一致。

downgrade 语义：先删 CHECK 约束再删列，顺序固定。既有供应商数据不受影响。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_suppliers_settlement_currency"
# 与 models.Supplier.__table_args__ 中的约束表达式逐字一致。
CONSTRAINT_SQL = (
    "settlement_currency IS NULL OR settlement_currency ~ '^[A-Z]{3}$'"
)


def upgrade() -> None:
    op.add_column(
        "suppliers",
        sa.Column("settlement_currency", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "suppliers",
        sa.text(CONSTRAINT_SQL),
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "suppliers", type_="check")
    op.drop_column("suppliers", "settlement_currency")