from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_risk_events_dedup_key"),
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name="ck_risk_events_severity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    dedup_key: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    event_subtype: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float)
    facts: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskEventSignal(Base):
    __tablename__ = "risk_event_signals"
    __table_args__ = (PrimaryKeyConstraint("event_id", "signal_id"),)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE")
    )
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("raw_signals.id", ondelete="CASCADE")
    )


class EventEntity(Base):
    __tablename__ = "event_entities"
    __table_args__ = (
        UniqueConstraint("event_id", "normalized_name", name="uq_event_entities_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    registry_no: Mapped[str | None] = mapped_column(Text)


class EventLocation(Base):
    __tablename__ = "event_locations"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "normalized_name", name="uq_event_locations_name"
        ),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_event_locations_coordinate_pair",
        ),
        CheckConstraint(
            "radius_km IS NULL OR (latitude IS NOT NULL AND radius_km > 0)",
            name="ck_event_locations_radius",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    radius_km: Mapped[float | None] = mapped_column(Float)


class SupplierEventMatch(Base):
    __tablename__ = "supplier_event_matches"
    __table_args__ = (
        UniqueConstraint("supplier_id", "event_id", name="uq_supplier_event_matches"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_supplier_event_matches_score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE")
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE")
    )
    match_type: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list[str]] = mapped_column(JSONB)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RiskAlert(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (
        Index(
            "uq_risk_alerts_current_match_id",
            "match_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
        CheckConstraint("level IN ('P1', 'P2', 'P3', 'P4')", name="ck_risk_alerts_level"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_risk_alerts_score"),
        CheckConstraint(
            "status IN ('current', 'expired')", name="ck_risk_alerts_status"
        ),
        Index("ix_risk_alerts_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_event_matches.id", ondelete="CASCADE")
    )
    level: Mapped[str] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer)
    score_detail: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RuleDimensionConfig(Base):
    """监控维度的运行时配置覆盖（可视化工作台编辑）。

    只存覆盖项；未覆盖的键沿用维度模块默认。引擎每次处理事件时重新合并，
    因此启停与参数修改即时生效（热更新）。
    """

    __tablename__ = "rule_dimension_configs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(Text, unique=True)
    label: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    config: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
