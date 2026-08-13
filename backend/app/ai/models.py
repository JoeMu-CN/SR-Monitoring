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
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.signals.models import RawSignal


class AIAnalysisRecord(Base):
    __tablename__ = "ai_analysis_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_ai_analysis_records_status",
        ),
        Index("ix_ai_analysis_records_signal_started", "signal_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("raw_signals.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    review_reason: Mapped[str | None] = mapped_column(Text)

    signal: Mapped[RawSignal] = relationship()
