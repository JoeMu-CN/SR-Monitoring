"""迁移天眼查多维度核查额度默认为每日 1000 / 每月 10000。

Revision ID: 0052
Revises: 0051

背景：0021 把天眼查 login_config 的 daily_limit/monthly_limit 写成历史默认值
80/900，沿用"每供应商每次 1 次"的旧口径。多维度核查上线后改为"每工具 1 次"，
账户权威额度为 1000/10000。本迁移只把仍精确等于历史默认值 80/900 的行升级为
1000/10000，并写 0052 专属标记，绝不覆盖运营自定义的额度。

upgrade 语义：
- 仅当 code='tianyancha' 且 login_config->>'daily_limit'='80'
  且 login_config->>'monthly_limit'='900' 时，把两个键改为 1000/10000，
  并写入 login_config.tyc_budget_migration='0052'。
- 使用 jsonb_set 与 ->> 文本比较：login_config 为 NULL、缺字段或非数字字符串
  时条件均为假；目标行缺失或 0 行匹配时静默结束，不报错。
- 保留 login_config 其它键（mode/secret_source/tyc_dimensions 等）与 JSONB 类型；
  未迁移的行不写入标记。

downgrade 语义（保守、可逆）：
- 仅还原同时满足 tyc_budget_migration='0052' 且 daily_limit=1000 且
  monthly_limit=10000 的行，改回 80/900 并移除该标记。
- 因此迁移前恰为自定义 1000/10000 的行（无标记）不会被误改；升级后运营改成
  700/7000 的行也不满足 1000/10000，不会被误改，仅由运营显式回退处置。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_MARKER = "0052"
OLD_DAILY_LIMIT = "80"
OLD_MONTHLY_LIMIT = "900"
NEW_DAILY_LIMIT = "1000"
NEW_MONTHLY_LIMIT = "10000"

_UPGRADE = sa.text(
    f"""
    UPDATE data_sources
    SET login_config = jsonb_set(
        jsonb_set(
            jsonb_set(
                login_config,
                '{{daily_limit}}',
                '{NEW_DAILY_LIMIT}'::jsonb,
                true
            ),
            '{{monthly_limit}}',
            '{NEW_MONTHLY_LIMIT}'::jsonb,
            true
        ),
        '{{tyc_budget_migration}}',
        '"{MIGRATION_MARKER}"'::jsonb,
        true
    )
    WHERE code = 'tianyancha'
      AND login_config->>'daily_limit' = '{OLD_DAILY_LIMIT}'
      AND login_config->>'monthly_limit' = '{OLD_MONTHLY_LIMIT}'
    """
)

_DOWNGRADE = sa.text(
    f"""
    UPDATE data_sources
    SET login_config = jsonb_set(
        jsonb_set(
            login_config - 'tyc_budget_migration',
            '{{daily_limit}}',
            '{OLD_DAILY_LIMIT}'::jsonb,
            true
        ),
        '{{monthly_limit}}',
        '{OLD_MONTHLY_LIMIT}'::jsonb,
        true
    )
    WHERE code = 'tianyancha'
      AND login_config->>'tyc_budget_migration' = '{MIGRATION_MARKER}'
      AND login_config->>'daily_limit' = '{NEW_DAILY_LIMIT}'
      AND login_config->>'monthly_limit' = '{NEW_MONTHLY_LIMIT}'
    """
)


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
