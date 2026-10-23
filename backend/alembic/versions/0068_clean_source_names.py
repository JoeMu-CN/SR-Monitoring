"""清理信源名称中的括号内容。

Revision ID: 0068
Revises: 0067

背景：
- ``data_sources.name`` 长期混入括号标识（如「统计局制造业 PMI（E1 宏观经济）」），
  括号里的内容只承载内部分类码，展示时应去掉。
- 前端展示层已统一调用 ``displaySourceName`` 去括号，后端写入入口也做了同样的
  规范化；本迁移负责把历史存量一次清干净，避免 API 直接返回旧值。

upgrade 语义：
- 仅处理名称中仍含半角/全角左括号（``（`` / ``(``）的行；
- 去掉「左括号 + 任意非右括号内容 + 右括号」，压缩连续空白为单空格并去首尾空白；
- 若去括号后名称为空，保留原值（不产生空名称，也不与 NOT NULL/非空校验冲突）；
- 重复执行幂等：处理过的名称不再含左括号，后续执行不命中任何行。

downgrade 语义：
- 空操作。原括号内容属于被清理的展示噪声，未做备份，无法可靠恢复。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 去括号 → 压缩空白 → 去首尾空白；UPDATE 与 WHERE 中的三段正则保持一致。
_CLEAN_NAMES = sa.text(
    """
    UPDATE data_sources
    SET name = btrim(
        regexp_replace(
            regexp_replace(name, '[（(][^）)]*[）)]', '', 'g'),
            '[[:space:]]+', ' ', 'g'
        )
    )
    WHERE name ~ '[（(]'
      AND btrim(
          regexp_replace(
              regexp_replace(name, '[（(][^）)]*[）)]', '', 'g'),
              '[[:space:]]+', ' ', 'g'
          )
      ) <> ''
    """
)


def upgrade() -> None:
    op.execute(_CLEAN_NAMES)


def downgrade() -> None:
    # 括号内容在清理时被丢弃且未留存备份，无法还原；保持现状即最小风险。
    pass
