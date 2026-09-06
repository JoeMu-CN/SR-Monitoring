"""风险信号、事件与提醒的有效期策略快照。

Revision ID: 0047
Revises: 0046
"""

# noqa: SIZE_OK — 单版可逆 DDL 必须集中保存完整约束及其对称回退顺序。

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_REASON = (
    "'{\"code\":\"legacy_unmigrated\",\"anchor_source\":\"legacy\","
    "\"details\":{}}'::jsonb"
)
PENDING_REASON = (
    "'{\"code\":\"pending_classification\",\"anchor_source\":\"collected_at\","
    "\"details\":{}}'::jsonb"
)
REASON_SHAPE_CHECK = """
jsonb_typeof(validity_reason) = 'object'
AND validity_reason ?& ARRAY['code', 'anchor_source', 'details']
AND validity_reason - ARRAY['code', 'anchor_source', 'details'] = '{}'::jsonb
AND jsonb_typeof(validity_reason->'code') = 'string'
AND validity_reason->>'code' <> ''
AND validity_reason->>'anchor_source' IN (
    'published_at', 'collected_at', 'official_valid_until', 'event_end', 'legacy'
)
AND jsonb_typeof(validity_reason->'details') = 'object'
"""
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
SOURCE_POLICY_CHECK = f"""
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


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("validity_policy", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.execute(
        """
        UPDATE data_sources
        SET validity_policy = jsonb_build_object(
            'mode', 'fixed_days', 'fixed_days', signal_validity_days
        )
        WHERE signal_validity_days IS NOT NULL
        """
    )
    op.create_check_constraint(
        "ck_data_sources_validity_policy", "data_sources", SOURCE_POLICY_CHECK
    )

    op.add_column("raw_signals", sa.Column("validity_profile", sa.Text(), nullable=True))
    op.add_column(
        "raw_signals",
        sa.Column(
            "validity_state",
            sa.Text(),
            server_default=sa.text("'pending_classification'"),
            nullable=False,
        ),
    )
    op.add_column(
        "raw_signals", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "raw_signals", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "raw_signals", sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("raw_signals", sa.Column("validity_mode", sa.Text(), nullable=True))
    op.add_column("raw_signals", sa.Column("validity_key", sa.Text(), nullable=True))
    op.add_column(
        "raw_signals",
        sa.Column(
            "lifecycle_action",
            sa.Text(),
            server_default=sa.text("'assert'"),
            nullable=False,
        ),
    )
    op.add_column(
        "raw_signals", sa.Column("validity_policy_version", sa.Text(), nullable=True)
    )
    op.add_column(
        "raw_signals",
        sa.Column(
            "validity_reason",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text(PENDING_REASON),
            nullable=False,
        ),
    )
    op.execute(
        f"""
        UPDATE raw_signals
        SET validity_state = 'legacy', validity_reason = {LEGACY_REASON}
        """
    )
    op.create_check_constraint(
        "ck_raw_signals_validity_profile",
        "raw_signals",
        f"validity_profile IS NULL OR validity_profile IN ({PROFILE_VALUES})",
    )
    op.create_check_constraint(
        "ck_raw_signals_validity_state",
        "raw_signals",
        "validity_state IN ('pending_classification','active','expired','superseded',"
        "'revoked','conflicted','legacy')",
    )
    op.create_check_constraint(
        "ck_raw_signals_validity_mode",
        "raw_signals",
        "validity_mode IS NULL OR validity_mode IN ('fixed_days','until_superseded',"
        "'until_revoked','event_end_plus_grace','indefinite')",
    )
    op.create_check_constraint(
        "ck_raw_signals_lifecycle_action",
        "raw_signals",
        "lifecycle_action IN ('assert','confirm','revoke','supersede')",
    )
    op.create_check_constraint(
        "ck_raw_signals_policy_snapshot",
        "raw_signals",
        "validity_state IN ('pending_classification','legacy') OR "
        "(validity_profile IS NOT NULL AND validity_mode IS NOT NULL "
        "AND valid_from IS NOT NULL AND validity_policy_version IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_raw_signals_deadline_mode",
        "raw_signals",
        "validity_state IN ('pending_classification','legacy') OR "
        "(validity_mode IN ('fixed_days','until_superseded','event_end_plus_grace') "
        "AND valid_until IS NOT NULL) OR validity_mode IN ('until_revoked','indefinite')",
    )
    op.create_check_constraint(
        "ck_raw_signals_supersession_key",
        "raw_signals",
        "validity_mode <> 'until_superseded' OR "
        "(validity_key IS NOT NULL AND btrim(validity_key) <> '')",
    )
    op.create_check_constraint(
        "ck_raw_signals_validity_times",
        "raw_signals",
        "(valid_until IS NULL OR (valid_from IS NOT NULL AND valid_until >= valid_from)) "
        "AND (review_due_at IS NULL OR "
        "(valid_from IS NOT NULL AND review_due_at >= valid_from))",
    )
    op.create_check_constraint(
        "ck_raw_signals_validity_reason", "raw_signals", REASON_SHAPE_CHECK
    )
    op.create_check_constraint(
        "ck_raw_signals_legacy_reason",
        "raw_signals",
        f"validity_state <> 'legacy' OR validity_reason = {LEGACY_REASON}",
    )
    op.create_index(
        "ix_raw_signals_validity_queue",
        "raw_signals",
        ["validity_state", "valid_until", "collected_at"],
    )
    op.create_index(
        "uq_raw_signals_active_supersession_key",
        "raw_signals",
        ["source_id", "validity_key"],
        unique=True,
        postgresql_where=sa.text(
            "validity_state = 'active' AND validity_mode = 'until_superseded'"
        ),
    )

    op.add_column(
        "risk_events",
        sa.Column(
            "validity_state",
            sa.Text(),
            server_default=sa.text("'legacy'"),
            nullable=False,
        ),
    )
    op.add_column(
        "risk_events", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "risk_events", sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "risk_events", sa.Column("validity_policy_version", sa.Text(), nullable=True)
    )
    op.add_column(
        "risk_events",
        sa.Column(
            "validity_reason",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text(LEGACY_REASON),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_risk_events_validity_state",
        "risk_events",
        "validity_state IN ('active','expired','legacy')",
    )
    op.create_check_constraint(
        "ck_risk_events_validity_reason", "risk_events", REASON_SHAPE_CHECK
    )
    op.create_check_constraint(
        "ck_risk_events_legacy_reason",
        "risk_events",
        f"validity_state <> 'legacy' OR "
        f"(validity_policy_version IS NULL AND validity_reason = {LEGACY_REASON})",
    )

    op.add_column(
        "risk_alerts",
        sa.Column(
            "expiry_kind",
            sa.Text(),
            server_default=sa.text("'legacy'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_risk_alerts_expiry_kind",
        "risk_alerts",
        "expiry_kind IN ('finite','unbounded','legacy')",
    )
    op.create_check_constraint(
        "ck_risk_alerts_expiry_value",
        "risk_alerts",
        "(expiry_kind = 'finite' AND expires_at IS NOT NULL) OR "
        "(expiry_kind = 'unbounded' AND expires_at IS NULL) OR expiry_kind = 'legacy'",
    )

    op.drop_constraint(
        "ck_notification_deliveries_status", "notification_deliveries", type_="check"
    )
    op.create_check_constraint(
        "ck_notification_deliveries_status",
        "notification_deliveries",
        "status IN ('success','failed','queued','merged','rate_limited',"
        "'quiet_suppressed','expired_suppressed')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM notification_deliveries
                WHERE status = 'expired_suppressed'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0047: expired_suppressed audit records must be preserved';
            END IF;
        END $$
        """
    )
    op.drop_constraint(
        "ck_notification_deliveries_status", "notification_deliveries", type_="check"
    )
    op.create_check_constraint(
        "ck_notification_deliveries_status",
        "notification_deliveries",
        "status IN ('success','failed','queued','merged','rate_limited','quiet_suppressed')",
    )

    op.drop_constraint("ck_risk_alerts_expiry_value", "risk_alerts", type_="check")
    op.drop_constraint("ck_risk_alerts_expiry_kind", "risk_alerts", type_="check")
    op.drop_column("risk_alerts", "expiry_kind")

    op.drop_constraint("ck_risk_events_legacy_reason", "risk_events", type_="check")
    op.drop_constraint("ck_risk_events_validity_reason", "risk_events", type_="check")
    op.drop_constraint("ck_risk_events_validity_state", "risk_events", type_="check")
    op.drop_column("risk_events", "validity_reason")
    op.drop_column("risk_events", "validity_policy_version")
    op.drop_column("risk_events", "review_due_at")
    op.drop_column("risk_events", "valid_until")
    op.drop_column("risk_events", "validity_state")

    op.drop_index("uq_raw_signals_active_supersession_key", table_name="raw_signals")
    op.drop_index("ix_raw_signals_validity_queue", table_name="raw_signals")
    for constraint_name in (
        "ck_raw_signals_legacy_reason",
        "ck_raw_signals_validity_reason",
        "ck_raw_signals_validity_times",
        "ck_raw_signals_supersession_key",
        "ck_raw_signals_deadline_mode",
        "ck_raw_signals_policy_snapshot",
        "ck_raw_signals_lifecycle_action",
        "ck_raw_signals_validity_mode",
        "ck_raw_signals_validity_state",
        "ck_raw_signals_validity_profile",
    ):
        op.drop_constraint(constraint_name, "raw_signals", type_="check")
    for column_name in (
        "validity_reason",
        "validity_policy_version",
        "lifecycle_action",
        "validity_key",
        "validity_mode",
        "review_due_at",
        "valid_until",
        "valid_from",
        "validity_state",
        "validity_profile",
    ):
        op.drop_column("raw_signals", column_name)

    op.drop_constraint("ck_data_sources_validity_policy", "data_sources", type_="check")
    op.drop_column("data_sources", "validity_policy")
