"""迁移天眼查策略为 until_superseded 并更新说明为周度多维度核查。

Revision ID: 0053
Revises: 0052

背景：
- 多维度核查报告改为周度落库（同周幂等、跨周替代）。报告的自动替代只在信源策略为
  until_superseded 时生效（见 signals/supersession.py 的 is_supersession_candidate），
  因此把 tianyancha 从 fixed_days 30 升级为 until_superseded 30。fixed_days=30 是
  兜底上限：周任务长期未跑时旧报告也会在 30 天后自然过期，不至永久有效。
- 定时核查已由「每天北京时间 06:00」改为「每周日/周一 06:00 按哈希分片核查」，
  本迁移同步清除 0051 写下的陈旧说明。

已知前态（两个字段都命中才整行迁移）：
- validity_policy：SQL NULL（0015/0046/0047 fresh chain 在 0052 的真实形态，不假定为
  fixed_days/30）或旧默认 {"mode":"fixed_days","fixed_days":30,"review_required":true}。
- description：仅 0051 文案（含「每天北京时间 06:00」的旧说明）。0015 建表即写非 NULL
  说明、0051 又无条件重写，故正常链路到 0052 时 description 不可能自然为 SQL NULL；
  SQL NULL 只可能是运营或异常改写，必须视为自定义，不得覆盖。
- 其余值视为运营自定义；任一字段自定义时整行不迁移，绝不覆盖运营值。

upgrade 语义（整行、带条件、先写 provenance）：
- 仅当 code='tianyancha' 且两个字段均处于上述已知前态时，才把 validity_policy 写为
  0053 目标 {"mode":"until_superseded","fixed_days":30,"review_required":true}，
  description 写为周度分片说明。
- 变更前把原始值写入 login_config.tyc_profile_validity_migration：
  {"revision":"0053","policy":<原值|JSON null>,"description":<原值|JSON null>}。
  两个键始终写入；JSON null 表示原值为 SQL NULL，与「键缺失」严格区分。
- 目标行缺失、值仍为目标态或任一字段自定义时 0 行更新，不报错；不触碰其它信源；
  重复执行天然幂等（迁移后值已不属于已知前态）。

downgrade 语义（marker 驱动、逐字段恢复）：
- 仅处理 login_config.tyc_profile_validity_migration 为对象、revision='0053' 且同时
  含 policy/description 两个键的行；缺 marker、revision 不符或键缺失的行不处理。
- 逐字段恢复：validity_policy 仍精确等于 0053 目标值时，才从 marker 还原原始值
  （marker 的 JSON null 还原为 SQL NULL，而非 JSON null）；description 同理
  （``->>`` 对 JSON null 返回 SQL NULL）。已被运营再次修改的字段保留现值。
- 有效 marker 无论如何都删除，缺失/无关 marker 的行保持原样。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_MARKER = "0053"
MARKER_KEY = "tyc_profile_validity_migration"
TARGET_POLICY: dict[str, object] = {
    "mode": "until_superseded",
    "fixed_days": 30,
    "review_required": True,
}
# 0052 时代业务默认；fresh chain 的 validity_policy 前态是 SQL NULL，二者都受支持。
PREVIOUS_POLICY: dict[str, object] = {
    "mode": "fixed_days",
    "fixed_days": 30,
    "review_required": True,
}
TARGET_DESCRIPTION = (
    "外部核查工具：运行密钥在信息源控制台配置并加密存库；启用后可手动按供应商核查，"
    "并按供应商哈希分片在每周日与周一北京时间 06:00 自动核查；不参与通用 cron 拉取式采集。"
)
PREVIOUS_DESCRIPTION = (
    "外部核查工具：运行密钥在信息源控制台配置并加密存库；启用后可手动批量核查全部启用供应商，"
    "并于每天北京时间 06:00 自动批量核查；不参与通用 cron 拉取式采集。"
)

_UPGRADE = sa.text(
    f"""
    UPDATE data_sources
    SET validity_policy = CAST(:target_policy AS jsonb),
        description = :target_description,
        login_config = jsonb_set(
            login_config,
            '{{{MARKER_KEY}}}',
            jsonb_build_object(
                'revision', :marker_revision,
                'policy', validity_policy,
                'description', description
            ),
            true
        )
    WHERE code = 'tianyancha'
      AND (
          validity_policy IS NULL
          OR validity_policy = CAST(:previous_policy AS jsonb)
      )
      AND description = :previous_description
    """
)

_DOWNGRADE = sa.text(
    f"""
    UPDATE data_sources
    SET validity_policy = CASE
            WHEN validity_policy = CAST(:target_policy AS jsonb)
            THEN NULLIF(
                login_config->'{MARKER_KEY}'->'policy',
                'null'::jsonb
            )
            ELSE validity_policy
        END,
        description = CASE
            WHEN description = :target_description
            THEN login_config->'{MARKER_KEY}'->>'description'
            ELSE description
        END,
        login_config = login_config - '{MARKER_KEY}'
    WHERE code = 'tianyancha'
      AND jsonb_typeof(login_config->'{MARKER_KEY}') = 'object'
      AND login_config->'{MARKER_KEY}'->>'revision' = :marker_revision
      AND login_config->'{MARKER_KEY}' ?& ARRAY['policy', 'description']
    """
)


def upgrade() -> None:
    op.execute(
        _UPGRADE.bindparams(
            target_policy=json.dumps(TARGET_POLICY, ensure_ascii=False),
            target_description=TARGET_DESCRIPTION,
            marker_revision=MIGRATION_MARKER,
            previous_policy=json.dumps(PREVIOUS_POLICY, ensure_ascii=False),
            previous_description=PREVIOUS_DESCRIPTION,
        )
    )


def downgrade() -> None:
    op.execute(
        _DOWNGRADE.bindparams(
            target_policy=json.dumps(TARGET_POLICY, ensure_ascii=False),
            target_description=TARGET_DESCRIPTION,
            marker_revision=MIGRATION_MARKER,
        )
    )
