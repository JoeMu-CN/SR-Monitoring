"""情报聚合报告表：政策/行业维度事件的 LLM 简要报告与风险提醒建议。

与风险提醒（risk_alerts）完全解耦：这里一行对应**一个风险事件在一个情报分类下
的一份报告**，不依赖是否匹配到供应商、也不产生等级或推送。

唯一约束取 ``(event_id, category)`` 而非单列 ``event_id``：分类由信源归属唯一
推导，正常情况下二者等价；但保留分类维度可以在维度声明调整后允许同一事件在新
分类下重新生成，同时仍在「同一事件 + 同一分类只生成一次」这条真正需要的约束上
兜底（重复生成由唯一约束拒绝，调度与查询均以它为准）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntelligenceReport(Base):
    __tablename__ = "intelligence_reports"
    __table_args__ = (
        UniqueConstraint("event_id", "category", name="uq_intelligence_reports_event"),
        CheckConstraint(
            "category IN ('policy', 'industry')", name="ck_intelligence_reports_category"
        ),
        CheckConstraint(
            "status IN ('ready', 'failed')", name="ck_intelligence_reports_status"
        ),
        Index(
            "ix_intelligence_reports_category_generated",
            "category",
            "generated_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("risk_events.id", ondelete="CASCADE")
    )
    category: Mapped[str] = mapped_column(Text)
    report_zh: Mapped[str | None] = mapped_column(Text)
    suggestions: Mapped[list[str]] = mapped_column(
        JSONB, server_default="'[]'::jsonb", nullable=False
    )
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )