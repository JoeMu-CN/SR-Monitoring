from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

LEGACY_VALIDITY_REASON: Final = (
    "'{\"code\":\"legacy_unmigrated\",\"anchor_source\":\"legacy\","
    "\"details\":{}}'::jsonb"
)
PENDING_VALIDITY_REASON: Final = (
    "'{\"code\":\"pending_classification\",\"anchor_source\":\"collected_at\","
    "\"details\":{}}'::jsonb"
)
VALIDITY_REASON_SHAPE: Final = (
    "jsonb_typeof(validity_reason) = 'object' AND "
    "validity_reason ?& ARRAY['code','anchor_source','details'] AND "
    "validity_reason - ARRAY['code','anchor_source','details'] = '{}'::jsonb AND "
    "jsonb_typeof(validity_reason->'code') = 'string' AND "
    "validity_reason->>'code' <> '' AND validity_reason->>'anchor_source' IN "
    "('published_at','collected_at','official_valid_until','event_end','legacy') AND "
    "jsonb_typeof(validity_reason->'details') = 'object'"
)

# ck_data_sources_validity_policy 的规范文本：0047 文本去掉 review 键白名单与
# review_required 类型 / review_days 取值子句（信源级复核配置由 0054 迁移清理）。
# 必须与 alembic/versions/0054_source_validity_review_removal.py 的 SOURCE_POLICY_CHECK
# 保持一致；测试 test_source_validity_review_removal_migration.py 会做等价性断言。
DATA_SOURCE_VALIDITY_POLICY_PROFILE_VALUES: Final = """
'weather_alert', 'geological_hazard', 'public_health_restriction',
'industrial_accident', 'regional_resource_constraint', 'transport_disruption',
'public_security', 'armed_conflict', 'political_instability', 'sanctions',
'export_control', 'trade_tariff', 'policy_draft', 'regulatory_change',
'compliance_violation', 'judicial_case', 'adverse_registry', 'corporate_distress',
'bankruptcy_proceeding', 'cyber_incident', 'market_price_point',
'raw_material_shortage', 'monthly_macro_indicator', 'industry_capacity_shift',
'reputation_event', 'supplier_performance_incident', 'other'
"""
DATA_SOURCE_VALIDITY_POLICY_CHECK: Final = f"""
validity_policy IS NULL OR (
    jsonb_typeof(validity_policy) = 'object'
    AND validity_policy ? 'mode'
    AND validity_policy - ARRAY[
        'profile', 'mode', 'fixed_days', 'grace_days', 'critical_grace_days'
    ] = '{{}}'::jsonb
    AND (NOT validity_policy ? 'profile' OR validity_policy->>'profile' IN (
{DATA_SOURCE_VALIDITY_POLICY_PROFILE_VALUES}))
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


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            "credibility BETWEEN 0 AND 100", name="ck_data_sources_credibility"
        ),
        CheckConstraint(
            DATA_SOURCE_VALIDITY_POLICY_CHECK,
            name="ck_data_sources_validity_policy",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    credibility: Mapped[int] = mapped_column(SmallInteger)
    schedule: Mapped[str | None] = mapped_column(Text)
    endpoint_url: Mapped[str | None] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    login_config: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    credential_ref: Mapped[str | None] = mapped_column(Text)
    api_key_hash: Mapped[str | None] = mapped_column(Text)
    api_key_last4: Mapped[str | None] = mapped_column(Text)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    adapter_config: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    adapter_status: Mapped[str] = mapped_column(Text, server_default=text("'unconfigured'"))
    adapter_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    adapter_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # 信源级信号有效期（天）：NULL=永久有效；正整数=信号自发生起 N 天内有效，
    # 过期后仅留库，不再计入有效记录、风险提醒按该时长失效。
    signal_validity_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # none_as_null：显式 None 必须落为 SQL NULL；JSON null 会绕过
    # ck_data_sources_validity_policy 的 IS NULL 分支。
    validity_policy: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    runs: Mapped[list["CollectionRun"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
    signals: Mapped[list["RawSignal"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
    audit_logs: Mapped[list["DataSourceAuditLog"]] = relationship(
        back_populates="source", passive_deletes=True
    )

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key_hash)

    @property
    def api_key_hint(self) -> str | None:
        return f"••••{self.api_key_last4}" if self.api_key_last4 else None


class SourceHostAccess(Base):
    """跨 Web 与 Scheduler 共享的外部域名访问状态。"""

    __tablename__ = "source_host_access"

    hostname: Mapped[str] = mapped_column(Text, primary_key=True)
    next_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error_kind: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DataSourceAuditLog(Base):
    __tablename__ = "data_source_audit_logs"
    __table_args__ = (Index("ix_data_source_audit_logs_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text)
    actor_role: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    source: Mapped[DataSource | None] = relationship(back_populates="audit_logs")


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_collection_runs_status",
        ),
        CheckConstraint(
            "snapshot_quality IN ('complete', 'partial', 'empty', 'failed')",
            name="ck_collection_runs_snapshot_quality",
        ),
        CheckConstraint(
            "member_count >= 0 AND new_member_count >= 0 AND revoked_member_count >= 0",
            name="ck_collection_runs_member_counts",
        ),
        CheckConstraint(
            "quarantine_round >= 0", name="ck_collection_runs_quarantine_round"
        ),
        Index("ix_collection_runs_source_started", "source_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    fetched_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duplicate_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    # 完整快照成员状态机跟踪字段
    snapshot_complete: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    snapshot_hash: Mapped[str | None] = mapped_column(Text)
    snapshot_quality: Mapped[str] = mapped_column(
        Text, server_default=text("'partial'")
    )
    member_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    new_member_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    revoked_member_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    quarantine_round: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    quarantine_baseline_hash: Mapped[str | None] = mapped_column(Text)
    quarantine_missing_hash: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    source: Mapped[DataSource] = relationship(back_populates="runs")


class SourceMemberState(Base):
    """名单成员状态：以 source_id + member_key 唯一跟踪成员生命周期。"""

    __tablename__ = "source_member_states"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "member_key", name="uq_source_member_states_source_key"
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_source_member_states_status",
        ),
        CheckConstraint(
            "consecutive_missing >= 0",
            name="ck_source_member_states_consecutive_missing",
        ),
        CheckConstraint(
            "quarantine_round >= 0", name="ck_source_member_states_quarantine_round"
        ),
        CheckConstraint(
            "btrim(member_key) <> ''", name="ck_source_member_states_member_key"
        ),
        Index(
            "ix_source_member_states_source_status", "source_id", "status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE")
    )
    member_key: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consecutive_missing: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    baseline_snapshot_hash: Mapped[str] = mapped_column(Text)
    quarantine_round: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RawSignal(Base):
    __tablename__ = "raw_signals"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "fingerprint", name="uq_raw_signals_source_fingerprint"
        ),
        Index("ix_raw_signals_source_published", "source_id", "published_at"),
        CheckConstraint(
            "validity_profile IS NULL OR validity_profile IN "
            "('weather_alert','geological_hazard','public_health_restriction',"
            "'industrial_accident','regional_resource_constraint','transport_disruption',"
            "'public_security','armed_conflict','political_instability','sanctions',"
            "'export_control','trade_tariff','policy_draft','regulatory_change',"
            "'compliance_violation','judicial_case','adverse_registry','corporate_distress',"
            "'bankruptcy_proceeding','cyber_incident','market_price_point',"
            "'raw_material_shortage','monthly_macro_indicator','industry_capacity_shift',"
            "'reputation_event','supplier_performance_incident','other')",
            name="ck_raw_signals_validity_profile",
        ),
        CheckConstraint(
            "validity_state IN ('pending_classification','active','expired','superseded',"
            "'revoked','conflicted','legacy')",
            name="ck_raw_signals_validity_state",
        ),
        CheckConstraint(
            "validity_mode IS NULL OR validity_mode IN ('fixed_days','until_superseded',"
            "'until_revoked','event_end_plus_grace','indefinite')",
            name="ck_raw_signals_validity_mode",
        ),
        CheckConstraint(
            "lifecycle_action IN ('assert','confirm','revoke','supersede')",
            name="ck_raw_signals_lifecycle_action",
        ),
        CheckConstraint(
            "validity_state IN ('pending_classification','legacy') OR "
            "(validity_profile IS NOT NULL AND validity_mode IS NOT NULL "
            "AND valid_from IS NOT NULL AND validity_policy_version IS NOT NULL)",
            name="ck_raw_signals_policy_snapshot",
        ),
        CheckConstraint(
            "validity_state IN ('pending_classification','legacy') OR "
            "(validity_mode IN ('fixed_days','until_superseded','event_end_plus_grace') "
            "AND valid_until IS NOT NULL) OR validity_mode IN ('until_revoked','indefinite')",
            name="ck_raw_signals_deadline_mode",
        ),
        CheckConstraint(
            "validity_mode <> 'until_superseded' OR "
            "(validity_key IS NOT NULL AND btrim(validity_key) <> '')",
            name="ck_raw_signals_supersession_key",
        ),
        CheckConstraint(
            "(valid_until IS NULL OR (valid_from IS NOT NULL AND valid_until >= valid_from)) "
            "AND (review_due_at IS NULL OR "
            "(valid_from IS NOT NULL AND review_due_at >= valid_from))",
            name="ck_raw_signals_validity_times",
        ),
        CheckConstraint(VALIDITY_REASON_SHAPE, name="ck_raw_signals_validity_reason"),
        CheckConstraint(
            f"validity_state <> 'legacy' OR validity_reason = {LEGACY_VALIDITY_REASON}",
            name="ck_raw_signals_legacy_reason",
        ),
        Index(
            "ix_raw_signals_validity_queue",
            "validity_state",
            "valid_until",
            "collected_at",
        ),
        Index(
            "uq_raw_signals_active_supersession_key",
            "source_id",
            "validity_key",
            unique=True,
            postgresql_where=text(
                "validity_state = 'active' AND validity_mode = 'until_superseded'"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE")
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    fingerprint: Mapped[str] = mapped_column(Text)
    raw_data: Mapped[dict[str, object]] = mapped_column(JSONB)
    validity_profile: Mapped[str | None] = mapped_column(Text)
    validity_state: Mapped[str] = mapped_column(
        Text, server_default=text("'pending_classification'")
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validity_mode: Mapped[str | None] = mapped_column(Text)
    validity_key: Mapped[str | None] = mapped_column(Text)
    lifecycle_action: Mapped[str] = mapped_column(Text, server_default=text("'assert'"))
    validity_policy_version: Mapped[str | None] = mapped_column(Text)
    validity_reason: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text(PENDING_VALIDITY_REASON)
    )

    source: Mapped[DataSource] = relationship(back_populates="signals")
