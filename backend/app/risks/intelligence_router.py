"""情报聚合只读 API：政策情报 / 行业情报的 LLM 报告与风险提醒建议。

- ``GET /api/v1/intelligence?tab=policy|industry``：按情报分类分页列出**已生成
  （status='ready'）**的报告。失败行不出现在列表（页面是报告页，不展示失败噪声），
  但仍可通过详情接口按 id 查看错误原因。
- ``GET /api/v1/intelligence/{id}``：单条详情，含 failed 行的 error。

权限复用 ``PERM_RISK_VIEW`` 与 risks 包的依赖别名：情报只读，不引入新的权限点，
也不引入任何写接口（生成只由 Scheduler 触发）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import PERM_RISK_VIEW, require_permission
from app.database import get_session
from app.risks.intelligence_models import IntelligenceReport
from app.risks.intelligence_schemas import (
    IntelligenceReportListResponse,
    IntelligenceReportRead,
)
from app.risks.models import RiskEvent, RiskEventSignal
from app.signals.models import DataSource, RawSignal

router = APIRouter(prefix="/api/v1", tags=["情报聚合"])
SessionDependency = Annotated[Session, Depends(get_session)]
RiskView = Annotated[User, Depends(require_permission(PERM_RISK_VIEW))]


def _report_rows(
    session: Session, rows: list[tuple[IntelligenceReport, RiskEvent]]
) -> list[IntelligenceReportRead]:
    """批量取事件的首条信号与信源，避免逐条查询；孤儿事件用安全默认值兜底。"""
    signals: dict[int, RawSignal] = {}
    if rows:
        signal_rows = session.execute(
            select(RiskEventSignal.event_id, RawSignal)
            .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
            .where(
                RiskEventSignal.event_id.in_([report.event_id for report, _ in rows])
            )
            .order_by(
                RiskEventSignal.event_id,
                RawSignal.published_at.desc().nullslast(),
                RawSignal.id.desc(),
            )
        ).all()
        for event_id, signal in signal_rows:
            signals.setdefault(event_id, signal)

    source_ids = {signal.source_id for signal in signals.values()}
    sources = {
        source.id: source
        for source in session.scalars(
            select(DataSource).where(DataSource.id.in_(source_ids))
        )
    } if source_ids else {}

    items: list[IntelligenceReportRead] = []
    for report, event in rows:
        signal = signals.get(event.id)
        source = sources.get(signal.source_id) if signal is not None else None
        items.append(
            IntelligenceReportRead(
                id=report.id,
                event_id=event.id,
                category=report.category,
                status=report.status,
                title=signal.title if signal is not None else "信号源已失效",
                event_summary=event.summary,
                event_type=event.event_type,
                event_subtype=event.event_subtype,
                event_start_at=event.start_at,
                source_code=source.code if source is not None else "unknown",
                source_name=source.name if source is not None else "未知信源",
                source_url=signal.url if signal is not None else None,
                published_at=signal.published_at if signal is not None else None,
                report_zh=report.report_zh,
                suggestions=list(report.suggestions or []),
                provider=report.provider,
                model=report.model,
                prompt_version=report.prompt_version,
                error=report.error,
                generated_at=report.generated_at,
            )
        )
    return items


@router.get("/intelligence", response_model=IntelligenceReportListResponse)
def list_intelligence_reports(
    session: SessionDependency,
    _user: RiskView,
    tab: str = Query("policy", pattern="^(policy|industry)$"),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IntelligenceReportListResponse:
    ready = IntelligenceReport.status == "ready"
    total = (
        session.scalar(
            select(func.count())
            .select_from(IntelligenceReport)
            .where(IntelligenceReport.category == tab, ready)
        )
        or 0
    )
    rows: list[Any] = list(
        session.execute(
            select(IntelligenceReport, RiskEvent)
            .join(RiskEvent, RiskEvent.id == IntelligenceReport.event_id)
            .where(IntelligenceReport.category == tab, ready)
            .order_by(
                IntelligenceReport.generated_at.desc().nullslast(),
                IntelligenceReport.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return IntelligenceReportListResponse(
        items=_report_rows(session, rows),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/intelligence/{report_id}", response_model=IntelligenceReportRead)
def get_intelligence_report(
    report_id: int, session: SessionDependency, _user: RiskView
) -> IntelligenceReportRead:
    row = session.execute(
        select(IntelligenceReport, RiskEvent)
        .join(RiskEvent, RiskEvent.id == IntelligenceReport.event_id)
        .where(IntelligenceReport.id == report_id)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="情报报告不存在"
        )
    return _report_rows(session, [row])[0]