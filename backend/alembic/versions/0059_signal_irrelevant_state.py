"""信号显式终态 irrelevant：消除 filtered 信号长期滞留 pending_classification。

Revision ID: 0059
Revises: 0058

背景：
- 确定性预筛（相关性预筛 / 清单未命中 / 结构化分级）把「不相关」信号写成
  ``status='succeeded'`` 且 ``result`` 为空（SQL NULL 或 JSON null）的分析记录，
  但信号本身一直留在 ``pending_classification``：队列谓词只能靠「终态过滤信号」子查询
  把它排除，监控口径里仍是 pending，既不进入人工复核统计，也无从区分「已判定不相关」
  与「尚未处理」。
- 新增终态 ``irrelevant``：被确定性预筛判定为不相关的信号显式落终态，不计有效、
  不再进入分析队列。

约束：
- ``ck_raw_signals_validity_state`` 白名单加入 ``irrelevant``。
- ``ck_raw_signals_policy_snapshot`` 与 ``ck_raw_signals_deadline_mode`` 的「无需策略
  快照 / 截止时间」豁免集从 ('pending_classification','legacy') 扩为
  ('pending_classification','irrelevant','legacy')：预筛发生在 AI 分类之前，信号尚未
  取得策略快照。三条约束必须同时改，否则写入与回填都会被 CheckViolation 拒绝。
- 判活谓词与队列谓词不改：``valid_signal_condition`` 与
  ``pending_signal_candidate_filters`` 都是 ACTIVE/LEGACY 与
  ('pending_classification','active') 的 allow-list，自动排除 irrelevant；
  ``_terminal_filtered_signal_ids()`` 作为纵深防御保留。

upgrade 语义（顺序固定）：
1. drop 3 条旧 CHECK；
2. create 3 条新 CHECK（含 irrelevant 与扩大的豁免集）；
3. 单条幂等 UPDATE 回填：仅把「pending_classification 且存在终态过滤分析记录
   （succeeded + result 为 SQL NULL 或 JSON null）且 reason code 不是
   classification_failed」的信号改为 irrelevant，并同步写入
   ``{"code":"filtered_out","anchor_source":published_at/collected_at,
   "details":{"filter":"prefilter","reason":…}}``。
   谓词与 ``_terminal_filtered_signal_ids()`` 的空结果判定逐字一致，不另写放宽或
   收紧的第二份；classification_failed 的 118 条保持 pending 供人工复核。
   再次执行无匹配行，天然幂等。

downgrade 语义：
1. 守卫：若存在 reason code 不是 filtered_out 的 irrelevant 信号（未知生产者写入的
   状态语义），RAISE EXCEPTION 拒绝降级，避免静默丢弃未知语义；
2. drop 3 条新 CHECK（ADD CONSTRAINT 会校验既有行，仍为 irrelevant 的行会被 0047 旧
   白名单拒绝，因此必须先改数据再建约束）；
3. 把 irrelevant 行改回 pending_classification，并按 0047 既有惯例写回
   PENDING_REASON（filtered_out 的 details 在降级后不再保留，但过滤分析记录仍在，
   队列谓词的纵深防御继续排除它们）；
4. 重建 0047 原文 3 条 CHECK。
"""

# noqa: SIZE_OK — 单版可逆 DDL 必须集中保存完整约束及其对称回退顺序。

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0059 目标态 3 条 CHECK（与 app/signals/models.py 的 RawSignal 约束逐字一致）。
VALIDITY_STATE_CHECK = (
    "validity_state IN ('pending_classification','active','expired','superseded',"
    "'revoked','conflicted','irrelevant','legacy')"
)
POLICY_SNAPSHOT_CHECK = (
    "validity_state IN ('pending_classification','irrelevant','legacy') OR "
    "(validity_profile IS NOT NULL AND validity_mode IS NOT NULL "
    "AND valid_from IS NOT NULL AND validity_policy_version IS NOT NULL)"
)
DEADLINE_MODE_CHECK = (
    "validity_state IN ('pending_classification','irrelevant','legacy') OR "
    "(validity_mode IN ('fixed_days','until_superseded','event_end_plus_grace') "
    "AND valid_until IS NOT NULL) OR validity_mode IN ('until_revoked','indefinite')"
)

# 0047 原文 3 条 CHECK（downgrade 恢复目标；必须与 0047 逐字一致）。
LEGACY_VALIDITY_STATE_CHECK = (
    "validity_state IN ('pending_classification','active','expired','superseded',"
    "'revoked','conflicted','legacy')"
)
LEGACY_POLICY_SNAPSHOT_CHECK = (
    "validity_state IN ('pending_classification','legacy') OR "
    "(validity_profile IS NOT NULL AND validity_mode IS NOT NULL "
    "AND valid_from IS NOT NULL AND validity_policy_version IS NOT NULL)"
)
LEGACY_DEADLINE_MODE_CHECK = (
    "validity_state IN ('pending_classification','legacy') OR "
    "(validity_mode IN ('fixed_days','until_superseded','event_end_plus_grace') "
    "AND valid_until IS NOT NULL) OR validity_mode IN ('until_revoked','indefinite')"
)

PENDING_REASON = (
    "'{\"code\":\"pending_classification\",\"anchor_source\":\"collected_at\","
    "\"details\":{}}'::jsonb"
)

# 单条幂等回填：空结果判定与 _terminal_filtered_signal_ids() 完全一致
# （status='succeeded' AND result 为 SQL NULL 或 JSON null）。
BACKFILL_UPDATE = """
UPDATE raw_signals AS rs
SET validity_state = 'irrelevant',
    validity_reason = jsonb_build_object(
        'code', 'filtered_out',
        'anchor_source',
            CASE
                WHEN rs.published_at IS NOT NULL THEN 'published_at'
                ELSE 'collected_at'
            END,
        'details', jsonb_build_object(
            'filter', 'prefilter',
            'reason', '历史分析成功但结果为空：确定性预筛判定不相关'
        )
    )
WHERE rs.validity_state = 'pending_classification'
  AND rs.id IN (
      SELECT ar.signal_id
      FROM ai_analysis_records AS ar
      WHERE ar.status = 'succeeded'
        AND (ar.result IS NULL OR ar.result::text = 'null')
  )
  AND COALESCE(rs.validity_reason->>'code', '') <> 'classification_failed'
"""

DOWNGRADE_BACKFILL_UPDATE = f"""
UPDATE raw_signals
SET validity_state = 'pending_classification', validity_reason = {PENDING_REASON}
WHERE validity_state = 'irrelevant'
"""

DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM raw_signals
        WHERE validity_state = 'irrelevant'
          AND COALESCE(validity_reason->>'code', '') <> 'filtered_out'
    ) THEN
        RAISE EXCEPTION
            'cannot downgrade 0059: irrelevant signals with unknown reason codes '
            'must be reviewed first';
    END IF;
END $$
"""

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


def _drop_checks(checks: tuple[tuple[str, str], ...]) -> None:
    for name, _ in checks:
        op.drop_constraint(name, "raw_signals", type_="check")


def _create_checks(checks: tuple[tuple[str, str], ...]) -> None:
    for name, check in checks:
        op.create_check_constraint(name, "raw_signals", check)


def upgrade() -> None:
    # 先换约束再回填：新 CHECK 必须先放行 irrelevant，旧 CHECK 会直接拒绝该写入。
    _drop_checks(TARGET_CHECKS)
    _create_checks(TARGET_CHECKS)
    op.execute(BACKFILL_UPDATE)


def downgrade() -> None:
    op.execute(DOWNGRADE_GUARD)
    # 顺序固定：先删新 CHECK，再把 irrelevant 行改回 pending，最后重建 0047 原文 CHECK
    # （ADD CONSTRAINT 会校验既有行，仍为 irrelevant 会被旧白名单拒绝）。
    _drop_checks(LEGACY_CHECKS)
    op.execute(DOWNGRADE_BACKFILL_UPDATE)
    _create_checks(LEGACY_CHECKS)