from datetime import datetime

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


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            "credibility BETWEEN 0 AND 100", name="ck_data_sources_credibility"
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

    source: Mapped[DataSource] = relationship(back_populates="runs")


class RawSignal(Base):
    __tablename__ = "raw_signals"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "fingerprint", name="uq_raw_signals_source_fingerprint"
        ),
        Index("ix_raw_signals_source_published", "source_id", "published_at"),
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

    source: Mapped[DataSource] = relationship(back_populates="signals")
