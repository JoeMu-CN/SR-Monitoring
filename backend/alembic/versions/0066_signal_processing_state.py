"""信号状态拆分：validity_state 只表达生命周期，新增 processing_state 表达处理阶段。

Revision ID: 0066
Revises: 0065

背景：
- ``raw_signals.validity_state`` 此前一个字段混用了三种语义：生命周期（active /
  expired / superseded / revoked / conflicted / legacy）、处理阶段
  （pending_classification）与确定性过滤结论（irrelevant）。结果是「尚未处理」
  「判定业务无关」「行情未触发」「分类失败」都挤在同一列，读侧只能靠 reason code
  与终态过滤记录的子查询猜阶段，新增阶段也无处安放。
- 本次把语义正交拆开：``validity_state`` 只留生命周期，**生命周期未定时写 NULL**；
  新增 ``processing_state`` 表达处理进度与结论（待分类 / 已分析 / 已过滤 / 行情未
  触发 / 分类失败）。``pending_classification`` 与 ``irrelevant`` 两个旧取值保留在
  validity_state 白名单内一个版本，仅用于兼容历史数据与回滚。

upgrade 语义（顺序固定）：
1. 新增 ``processing_state text NOT NULL DEFAULT 'pending_classification'`` 与
   ``ck_raw_signals_processing_state``（5 值白名单）；既有行按默认值落到「待分类」，
   随后由回填修正。
2. 换 3 条既有 CHECK：``ck_raw_signals_validity_state`` 显式放行 NULL；
   ``ck_raw_signals_policy_snapshot`` / ``ck_raw_signals_deadline_mode`` 的
   「无需策略快照 / 截止时间」豁免集加入 ``validity_state IS NULL``。必须先换约束再
   回填，否则把 validity_state 置 NULL 的写入会被旧文本拒绝。
3. 同一迁移事务内回填（约 3.8 万行，逐条 UPDATE 各一条语句）：
   a. ``validity_state='pending_classification'`` → 生命周期置 NULL；
   b. ``validity_state='irrelevant'`` → 按 ``validity_reason.details.filter`` 推断
      处理阶段（market_indicator → no_trigger，其余 → filtered），并尽量还原生命
      周期（valid_until 未到 → active、已过 → expired；until_revoked / indefinite
      历史行 → active；其余 NULL）。旧语义里 irrelevant 覆盖了生命周期，这里只能
      按现存证据还原，无法还原的保持 NULL（未定）。
   c. 有 ``risk_event_signals`` 关联 → analyzed（覆盖 a/b：进入事件链是最终事实）；
   d. 有 ``market-eval-v1`` 分析记录且未 analyzed → no_trigger；
   e. ``validity_reason.code='classification_failed'`` → classification_failed。
   每条语句都带状态谓词，重复执行无匹配行，天然幂等。

downgrade 语义：
1. 先把新阶段映射回旧语义（旧 schema 无法表达「active + 已过滤」，一律回到
   irrelevant）：``processing_state IN ('filtered','no_trigger')`` → irrelevant；
   ``validity_state IS NULL`` 且阶段为待分类 / 分类失败 / 已分析 → pending_classification。
2. 再换回 0059 的 3 条 CHECK（ADD CONSTRAINT 会校验既有行，必须先把数据改回去），
   最后删 ``ck_raw_signals_processing_state`` 与 ``processing_state`` 列。
"""

# noqa: SIZE_OK — 单版可逆 DDL 必须集中保存完整约束及其对称回退顺序。

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0066 目标态约束（与 app/signals/models.py 的 RawSignal 约束逐字一致）。
VALIDITY_STATE_CHECK = (
    "validity_state IS NULL OR validity_state IN "
    "('pending_classification','active','expired','superseded',"
    "'revoked','conflicted','irrelevant','legacy')"
)
POLICY_SNAPSHOT_CHECK = (
    "validity_state IS NULL OR "
    "validity_state IN ('pending_classification','irrelevant','legacy') OR "
    "(validity_profile IS NOT NULL AND validity_mode IS NOT NULL "
    "AND valid_from IS NOT NULL AND validity_policy_version IS NOT NULL)"
)
DEADLINE_MODE_CHECK = (
    "validity_state IS NULL OR "
    "validity_state IN ('pending_classification','irrelevant','legacy') OR "
    "(validity_mode IN ('fixed_days','until_superseded','event_end_plus_grace') "
    "AND valid_until IS NOT NULL) OR validity_mode IN ('until_revoked','indefinite')"
)
PROCESSING_STATE_CHECK = (
    "processing_state IN ('pending_classification','analyzed','filtered',"
    "'no_trigger','classification_failed')"
)

# 0059 目标态约束（downgrade 恢复目标；必须与 0059 逐字一致）。
LEGACY_VALIDITY_STATE_CHECK = (
    "validity_state IN ('pending_classification','active','expired','superseded',"
    "'revoked','conflicted','irrelevant','legacy')"
)
LEGACY_POLICY_SNAPSHOT_CHECK = (
    "validity_state IN ('pending_classification','irrelevant','legacy') OR "
    "(validity_profile IS NOT NULL AND validity_mode IS NOT NULL "
    "AND valid_from IS NOT NULL AND validity_policy_version IS NOT NULL)"
)
LEGACY_DEADLINE_MODE_CHECK = (
    "validity_state IN ('pending_classification','irrelevant','legacy') OR "
    "(validity_mode IN ('fixed_days','until_superseded','event_end_plus_grace') "
    "AND valid_until IS NOT NULL) OR validity_mode IN ('until_revoked','indefinite')"
)

TARGET_CHECKS = (
    ("ck_raw_signals_validity_state", VALIDITY_STATE_CHECK),
    ("ck_raw_signals_policy_snapshot", POLICY_SNAPSHOT_CHECK),
    ("ck_raw_signals_deadline_mode", DEADLINE_MODE_CHECK),
)
LEGACY_CHECKS = (
    ("ck_raw_signals_validity_state", LEGACY_VALIDITY_STATE_CHECK),
    ("ck_raw_signals_policy_snapshot", LEGACY_POLICY_SNAPSHOT_CHECK),
    ("ck_raw_signals_deadline_mode", LEGACY_DEADLINE_MODE_CHECK),
)

# a. 待分类 → 生命周期置空（处理阶段由列默认值即 pending_classification 表达）。
BACKFILL_PENDING = """
UPDATE raw_signals
SET processing_state = 'pending_classification', validity_state = NULL
WHERE validity_state = 'pending_classification'
"""

# b. irrelevant → 推断处理阶段 + 还原生命周期。
BACKFILL_IRRELEVANT = """
UPDATE raw_signals AS rs
SET processing_state = CASE
        WHEN rs.validity_reason->'details'->>'filter' = 'market_indicator'
            THEN 'no_trigger'
        ELSE 'filtered'
    END,
    validity_state = CASE
        WHEN rs.valid_until IS NOT NULL AND rs.valid_until > now() THEN 'active'
        WHEN rs.valid_until IS NOT NULL AND rs.valid_until <= now() THEN 'expired'
        WHEN rs.validity_mode IN ('until_revoked', 'indefinite') THEN 'active'
        ELSE NULL
    END
WHERE rs.validity_state = 'irrelevant'
"""

# c. 有事件关联 → analyzed（进入事件链是最终事实，覆盖 a/b 的推断）。
BACKFILL_ANALYZED = """
UPDATE raw_signals
SET processing_state = 'analyzed'
WHERE id IN (SELECT signal_id FROM risk_event_signals)
"""

# d. 有行情评估记录且未进入事件链 → no_trigger（已确定性评估、未触发）。
BACKFILL_NO_TRIGGER = """
UPDATE raw_signals AS rs
SET processing_state = 'no_trigger'
WHERE rs.processing_state <> 'analyzed'
  AND EXISTS (
      SELECT 1
      FROM ai_analysis_records AS ar
      WHERE ar.signal_id = rs.id AND ar.model = 'market-eval-v1'
  )
"""

# e. 分类失败（放在最后，覆盖 a/d 的推断）。
BACKFILL_CLASSIFICATION_FAILED = """
UPDATE raw_signals
SET processing_state = 'classification_failed'
WHERE validity_reason->>'code' = 'classification_failed'
"""

BACKFILL_STATEMENTS = (
    BACKFILL_PENDING,
    BACKFILL_IRRELEVANT,
    BACKFILL_ANALYZED,
    BACKFILL_NO_TRIGGER,
    BACKFILL_CLASSIFICATION_FAILED,
)

# downgrade 第 1 步：把新阶段映射回旧语义（旧 schema 无法表达 active + 已过滤）。
DOWNGRADE_RESTORE_IRRELEVANT = """
UPDATE raw_signals
SET validity_state = 'irrelevant'
WHERE processing_state IN ('filtered', 'no_trigger')
"""

# 生命周期未定的行回到旧模型的 pending_classification；analyzed 理论上必有生命周期，
# 若出现（异常数据）也回落 pending，避免降级后读侧完全看不见这些行。
DOWNGRADE_RESTORE_PENDING = """
UPDATE raw_signals
SET validity_state = 'pending_classification'
WHERE validity_state IS NULL
  AND processing_state IN ('pending_classification', 'classification_failed', 'analyzed')
"""


def _drop_checks(checks: tuple[tuple[str, str], ...]) -> None:
    for name, _ in checks:
        op.drop_constraint(name, "raw_signals", type_="check")


def _create_checks(checks: tuple[tuple[str, str], ...]) -> None:
    for name, check in checks:
        op.create_check_constraint(name, "raw_signals", check)


def upgrade() -> None:
    op.add_column(
        "raw_signals",
        sa.Column(
            "processing_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending_classification'"),
        ),
    )
    # 生命周期未定改由 NULL 表达：0047 建列时的 NOT NULL 与
    # DEFAULT 'pending_classification' 必须同时去掉——保留默认值会让 ORM 显式写
    # None 的行被静默改写成 pending_classification（NULL 语义消失）。
    op.alter_column(
        "raw_signals",
        "validity_state",
        existing_type=sa.Text(),
        nullable=True,
        server_default=None,
    )
    op.create_check_constraint(
        "ck_raw_signals_processing_state", "raw_signals", PROCESSING_STATE_CHECK
    )
    # 先换约束再回填：新 CHECK 必须显式放行 validity_state IS NULL。
    _drop_checks(LEGACY_CHECKS)
    _create_checks(TARGET_CHECKS)
    for statement in BACKFILL_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # 顺序固定：先改数据（新阶段 → 旧语义），再换约束，最后删列。
    op.execute(DOWNGRADE_RESTORE_IRRELEVANT)
    op.execute(DOWNGRADE_RESTORE_PENDING)
    _drop_checks(TARGET_CHECKS)
    _create_checks(LEGACY_CHECKS)
    op.drop_constraint("ck_raw_signals_processing_state", "raw_signals", type_="check")
    op.drop_column("raw_signals", "processing_state")
    # 旧模型的 validity_state 是 NOT NULL + DEFAULT 'pending_classification'
    # （数据已在上面全部回填）。
    op.alter_column(
        "raw_signals",
        "validity_state",
        existing_type=sa.Text(),
        nullable=False,
        server_default=sa.text("'pending_classification'"),
    )
