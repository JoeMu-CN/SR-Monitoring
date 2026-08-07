from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.signals.adapter import (
    MAX_FILE_BYTES,
    ManualJsonAdapter,
    SignalFileValidationError,
    SourceAdapter,
)
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.schemas import (
    CollectionRunListResponse,
    CollectionRunRead,
    DataSourceRead,
    SignalImportSummary,
)
from app.signals.service import CollectionFailed, SourceNotCollectable, collect_source
from app.signals.sources import NmcWeatherAdapter, PullSourceAdapter

router = APIRouter(prefix="/api/v1", tags=["风险信号"])
SessionDependency = Annotated[Session, Depends(get_session)]
adapter: SourceAdapter = ManualJsonAdapter()


def build_pull_adapter(source_code: str) -> PullSourceAdapter:
    """按数据源编码构建拉取式适配器；不支持时抛 SourceNotCollectable。"""
    if source_code == NmcWeatherAdapter.source_code:
        return NmcWeatherAdapter()
    raise SourceNotCollectable(f"数据源 {source_code} 不支持手动拉取采集")


def get_manual_source(session: Session) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == adapter.source_code))
    if source is None or not source.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="手工 JSON 数据源不可用",
        )
    return source


def start_run(session: Session, source_id: int) -> CollectionRun:
    run = CollectionRun(source_id=source_id, status="running")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def fail_run(session: Session, run_id: int, message: str) -> None:
    session.rollback()
    run = session.get(CollectionRun, run_id)
    if run is not None:
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.error = message[:2000]
        session.commit()


@router.get("/sources", response_model=list[DataSourceRead])
def list_sources(session: SessionDependency) -> list[DataSource]:
    return list(session.scalars(select(DataSource).order_by(DataSource.id)))


@router.get("/collection-runs", response_model=CollectionRunListResponse)
def list_collection_runs(
    session: SessionDependency,
    source_id: int | None = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CollectionRunListResponse:
    filters = []
    if source_id is not None:
        filters.append(CollectionRun.source_id == source_id)
    if run_status is not None:
        if run_status not in {"running", "succeeded", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="状态必须是 running、succeeded 或 failed",
            )
        filters.append(CollectionRun.status == run_status)

    total = (
        session.scalar(select(func.count()).select_from(CollectionRun).where(*filters)) or 0
    )
    items = list(
        session.scalars(
            select(CollectionRun)
            .where(*filters)
            .order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CollectionRunListResponse(
        items=[CollectionRunRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/signals/import", response_model=SignalImportSummary)
async def import_signals(
    session: SessionDependency,
    file: Annotated[UploadFile, File(description="标准 UTF-8 JSON 风险信号文件")],
) -> SignalImportSummary:
    source = get_manual_source(session)
    run = start_run(session, source.id)
    try:
        filename = file.filename or ""
        if not filename.lower().endswith(".json"):
            raise SignalFileValidationError(
                [{"path": "文件", "message": "只支持 .json 文件"}]
            )
        data = await file.read(MAX_FILE_BYTES + 1)
        signals = adapter.parse(data)
        rows = [
            {
                "source_id": source.id,
                "external_id": signal.external_id,
                "title": signal.title,
                "content": signal.content,
                "url": str(signal.url) if signal.url else None,
                "published_at": signal.published_at,
                "fingerprint": adapter.fingerprint(signal),
                "raw_data": signal.model_dump(mode="json"),
            }
            for signal in signals
        ]
        result = session.execute(
            insert(RawSignal)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=[RawSignal.source_id, RawSignal.fingerprint]
            )
            .returning(RawSignal.id)
        )
        created = len(result.scalars().all())
        stored_run = session.get(CollectionRun, run.id)
        assert stored_run is not None
        stored_run.status = "succeeded"
        stored_run.finished_at = datetime.now(UTC)
        stored_run.fetched_count = len(signals)
        stored_run.created_count = created
        stored_run.duplicate_count = len(signals) - created
        session.commit()
    except SignalFileValidationError as exc:
        fail_run(session, run.id, str(exc.errors))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"errors": exc.errors},
        ) from exc
    except SQLAlchemyError as exc:
        fail_run(session, run.id, "数据库写入失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="风险信号导入失败",
        ) from exc

    return SignalImportSummary(
        run_id=run.id,
        fetched_signals=len(signals),
        created_signals=created,
        duplicate_signals=len(signals) - created,
    )


@router.post("/sources/{source_id}/run", response_model=CollectionRunRead)
def run_source_collection(
    source_id: int, session: SessionDependency
) -> CollectionRun:
    """手动触发一次数据源采集（仅支持 HTTP 拉取式数据源）。"""
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    if not source.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="数据源已停用"
        )
    try:
        pull_adapter = build_pull_adapter(source.code)
    except SourceNotCollectable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    try:
        run = collect_source(session, source, pull_adapter)
    except CollectionFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"采集失败: {exc}",
        ) from exc
    return run
