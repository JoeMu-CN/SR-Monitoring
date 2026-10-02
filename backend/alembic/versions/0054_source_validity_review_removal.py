"""清理信源级复核配置（review_days/review_required），provenance 可逆。

Revision ID: 0054
Revises: 0053

背景：
- 信源级有效期策略不再承载复核配置；复核语义由域层 profile 默认与运行时
  review_due_at 保留（见 signals/validity.py 与 SourceValidityPolicy）。
- 存量 data_sources.validity_policy 中可能残留 review_days/review_required；
  本迁移逐行清理，并把原策略写进 login_config provenance marker，保证可逆。

upgrade 语义（逐行、带条件、先写 provenance）：
- 对每个 validity_policy ?| ARRAY['review_days','review_required'] 的行：
  先写 login_config.source_validity_review_removal =
  {"revision":"0054","policy_before":<原值>,"policy_after":<去 review 值>}，
  再令 validity_policy = validity_policy - 'review_days' - 'review_required'。
  同一 UPDATE 内两个右侧表达式都取行旧值，等价于「先记录、后清理」。
- 未含 review 键的行 0 行更新、不写 marker；清理后不再命中，重复执行幂等。
- 随后重建 ck_data_sources_validity_policy：0047 文本去掉 review 键白名单与
  review_required 类型 / review_days 取值子句，残留 review 键直接被约束拒绝。

downgrade 语义（顺序固定，同一迁移事务内）：
1. 先 DROP ck_data_sources_validity_policy（新约束排除 review 键，若仍生效则写回
   policy_before 会报错）；
2. 仅当 validity_policy 精确等于 marker.policy_after 时恢复 marker.policy_before；
   有效 marker 无论字段是否命中都删除（同 0053 语义）；已被运营再次修改的行保留
   现值，缺失/无效 marker 的行 no-op；
3. 再创建 0047 的旧约束文本，保证 0053 的精确等值降级在 0054 之后仍可用。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_MARKER = "0054"
MARKER_KEY = "source_validity_review_removal"
PROFILE_VALUES = """
'weather_alert', 'geological_hazard', 'public_health_restriction',
'industrial_accident', 'regional_resource_constraint', 'transport_disruption',
'public_security', 'armed_conflict', 'political_instability', 'sanctions',
'export_control', 'trade_tariff', 'policy_draft', 'regulatory_change',
'compliance_violation', 'judicial_case', 'adverse_registry', 'corporate_distress',
'bankruptcy_proceeding', 'cyber_incident', 'market_price_point',
'raw_material_shortage', 'monthly_macro_indicator', 'industry_capacity_shift',
'reputation_event', 'supplier_performance_incident', 'other'
"""
# 0047 文本去掉 review 键白名单与 review_required 类型 / review_days 取值子句。
SOURCE_POLICY_CHECK = f"""
validity_policy IS NULL OR (
    jsonb_typeof(validity_policy) = 'object'
    AND validity_policy ? 'mode'
    AND validity_policy - ARRAY[
        'profile', 'mode', 'fixed_days', 'grace_days', 'critical_grace_days'
    ] = '{{}}'::jsonb
    AND (NOT validity_policy ? 'profile' OR validity_policy->>'profile' IN ({PROFILE_VALUES}))
    AND CASE validity_policy->>'mode'
        WHEN 'fixed_days' THEN
            validity_policy ? 'fixed_days'
            AND validity_policy->>'fixed_days' ~ '^[0-9]+$'
            AND (validity_policy->>'fixed_days')::integer BETWEEN 1 AND 3650
            AND NOT validity_policy ?| ARRAY['grace_days', 'critical_grace_days']
        WHEN 'until_superseded' THEN
            validity_policy ? 'fixed_days'
            AND validity_policy->>'fixed_days' ~ '^[0-9]+$'
            AND (validity_policy->>'fixed_days')::integer BETWEEN 1 AND 3650
            AND NOT validity_policy ?| ARRAY['grace_days', 'critical_grace_days']
        WHEN 'until_revoked' THEN
            NOT validity_policy ?| ARRAY['fixed_days', 'grace_days', 'critical_grace_days']
        WHEN 'event_end_plus_grace' THEN
            validity_policy ? 'grace_days'
            AND validity_policy->>'grace_days' ~ '^[0-9]+$'
            AND (validity_policy->>'grace_days')::integer BETWEEN 1 AND 3650
            AND NOT validity_policy ?| ARRAY['fixed_days']
            AND (
                NOT validity_policy ? 'critical_grace_days'
                OR (
                    validity_policy->>'critical_grace_days' ~ '^[0-9]+$'
                    AND (validity_policy->>'critical_grace_days')::integer BETWEEN 1 AND 3650
                )
            )
        WHEN 'indefinite' THEN
            NOT validity_policy ?| ARRAY['fixed_days', 'grace_days', 'critical_grace_days']
        ELSE false
    END
)
"""
# 0047 原文（含 review 子句），downgrade 时精确恢复。
LEGACY_SOURCE_POLICY_CHECK = f"""
validity_policy IS NULL OR (
    jsonb_typeof(validity_policy) = 'object'
    AND validity_policy ? 'mode'
    AND validity_policy - ARRAY[
        'profile', 'mode', 'fixed_days', 'grace_days', 'critical_grace_days',
        'review_days', 'review_required'
    ] = '{{}}'::jsonb
    AND (NOT validity_policy ? 'profile' OR validity_policy->>'profile' IN ({PROFILE_VALUES}))
    AND (
        NOT validity_policy ? 'review_required'
        OR jsonb_typeof(validity_policy->'review_required') = 'boolean'
    )
    AND CASE validity_policy->>'mode'
        WHEN 'fixed_days' THEN
            validity_policy ? 'fixed_days'
            AND validity_policy->>'fixed_days' ~ '^[0-9]+$'
            AND (validity_policy->>'fixed_days')::integer BETWEEN 1 AND 3650
            AND NOT validity_policy ?| ARRAY['grace_days', 'critical_grace_days', 'review_days']
        WHEN 'until_superseded' THEN
            validity_policy ? 'fixed_days'
            AND validity_policy->>'fixed_days' ~ '^[0-9]+$'
            AND (validity_policy->>'fixed_days')::integer BETWEEN 1 AND 3650
            AND NOT validity_policy ?| ARRAY['grace_days', 'critical_grace_days', 'review_days']
        WHEN 'until_revoked' THEN
            NOT validity_policy ?| ARRAY['fixed_days', 'grace_days', 'critical_grace_days']
            AND (
                NOT validity_policy ? 'review_days'
                OR (
                    validity_policy->>'review_days' ~ '^[0-9]+$'
                    AND (validity_policy->>'review_days')::integer BETWEEN 1 AND 3650
                )
            )
        WHEN 'event_end_plus_grace' THEN
            validity_policy ? 'grace_days'
            AND validity_policy->>'grace_days' ~ '^[0-9]+$'
            AND (validity_policy->>'grace_days')::integer BETWEEN 1 AND 3650
            AND NOT validity_policy ?| ARRAY['fixed_days', 'review_days']
            AND (
                NOT validity_policy ? 'critical_grace_days'
                OR (
                    validity_policy->>'critical_grace_days' ~ '^[0-9]+$'
                    AND (validity_policy->>'critical_grace_days')::integer BETWEEN 1 AND 3650
                )
            )
        WHEN 'indefinite' THEN
            NOT validity_policy ?| ARRAY[
                'fixed_days', 'grace_days', 'critical_grace_days', 'review_days'
            ]
            AND COALESCE((validity_policy->>'review_required')::boolean, false) = false
        ELSE false
    END
)
"""

# 逐行先写 provenance，再减键清理；两个右侧表达式均取行旧值。
_UPGRADE = sa.text(
    f"""
    UPDATE data_sources
    SET login_config = jsonb_set(
            login_config,
            '{{{MARKER_KEY}}}',
            jsonb_build_object(
                'revision', :marker_revision,
                'policy_before', validity_policy,
                'policy_after', validity_policy - 'review_days' - 'review_required'
            ),
            true
        ),
        validity_policy = validity_policy - 'review_days' - 'review_required'
    WHERE validity_policy ?| ARRAY['review_days', 'review_required']
    """
)

# 仅当值仍等于 policy_after 时恢复 policy_before；有效 marker 一律删除。
_DOWNGRADE = sa.text(
    f"""
    UPDATE data_sources
    SET validity_policy = CASE
            WHEN validity_policy = login_config->'{MARKER_KEY}'->'policy_after'
            THEN login_config->'{MARKER_KEY}'->'policy_before'
            ELSE validity_policy
        END,
        login_config = login_config - '{MARKER_KEY}'
    WHERE jsonb_typeof(login_config->'{MARKER_KEY}') = 'object'
      AND login_config->'{MARKER_KEY}'->>'revision' = :marker_revision
      AND login_config->'{MARKER_KEY}' ?& ARRAY['policy_before', 'policy_after']
    """
)


def upgrade() -> None:
    op.execute(_UPGRADE.bindparams(marker_revision=MIGRATION_MARKER))
    op.drop_constraint("ck_data_sources_validity_policy", "data_sources", type_="check")
    op.create_check_constraint(
        "ck_data_sources_validity_policy", "data_sources", SOURCE_POLICY_CHECK
    )


def downgrade() -> None:
    # 顺序固定：先删新约束（否则写回 review 键会被拒绝），再逐行恢复，最后建旧约束。
    op.drop_constraint("ck_data_sources_validity_policy", "data_sources", type_="check")
    op.execute(_DOWNGRADE.bindparams(marker_revision=MIGRATION_MARKER))
    op.create_check_constraint(
        "ck_data_sources_validity_policy", "data_sources", LEGACY_SOURCE_POLICY_CHECK
    )
