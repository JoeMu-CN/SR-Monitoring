import hashlib
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config
from app.auth.models import User
from app.auth.security import (
    PERM_BUSINESS_AUDIT_VIEW,
    PERM_COLLECTION_TRIGGER,
    PERM_SIGNAL_IMPORT,
    PERM_SOURCE_MANAGE,
    PERM_SOURCE_STATUS_VIEW,
    require_permission,
    verify_csrf,
)
from app.database import get_session
from app.signals.adapter import (
    MAX_FILE_BYTES,
    ManualJsonAdapter,
    SignalFileValidationError,
    SourceAdapter,
)
from app.signals.declarative import (
    AdapterSpec,
    DeclarativeSourceAdapter,
    preview_adapter,
)
from app.signals.models import (
    CollectionRun,
    DataSource,
    DataSourceAuditLog,
    RawSignal,
    SourceHostAccess,
)
from app.signals.schemas import (
    AdapterPreviewRequest,
    AdapterPreviewResponse,
    CollectionRunListResponse,
    CollectionRunRead,
    DataSourceAuditLogListResponse,
    DataSourceAuditLogRead,
    DataSourceRead,
    DataSourceSummaryRead,
    DataSourceUpdate,
    DataSourceWrite,
    SignalImportSummary,
)
from app.signals.secret_store import decrypt_secret, encrypt_secret
from app.signals.service import CollectionFailed, SourceNotCollectable, collect_source
from app.signals.sources import (
    BisEntityListAdapter,
    CommodityFuturesAdapter,
    CustomsAnnouncementAdapter,
    EuComplianceAdapter,
    EuOfficialJournalAdapter,
    FmprcPressAdapter,
    FxRatesAdapter,
    MofcomEntityDetailAdapter,
    NmcWeatherAdapter,
    OfacSdnAdapter,
    PbcLprAdapter,
    PullSourceAdapter,
    SourceFetchError,
    SseShippingAdapter,
    StatsPmiAdapter,
    UflpaEntityAdapter,
    WtoNewsAdapter,
)

router = APIRouter(prefix="/api/v1", tags=["风险信号"])
SessionDependency = Annotated[Session, Depends(get_session)]
SourceStatusView = Annotated[User, Depends(require_permission(PERM_SOURCE_STATUS_VIEW))]
SourceManage = Annotated[User, Depends(require_permission(PERM_SOURCE_MANAGE))]
CollectionTrigger = Annotated[User, Depends(require_permission(PERM_COLLECTION_TRIGGER))]
SignalImport = Annotated[User, Depends(require_permission(PERM_SIGNAL_IMPORT))]
BusinessAuditView = Annotated[User, Depends(require_permission(PERM_BUSINESS_AUDIT_VIEW))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]
adapter: SourceAdapter = ManualJsonAdapter()


def _validate_schedule(schedule: str | None) -> None:
    if schedule is None:
        return
    if len(schedule.split()) != 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="调度周期必须是 5 段 cron 表达式（分 时 日 月 周）",
        )


def _api_key_fields(api_key: str | None) -> dict[str, str | None]:
    if not api_key:
        return {}
    value = api_key.strip()
    return {
        "api_key_hash": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "api_key_last4": value[-4:],
        "api_key_encrypted": encrypt_secret(value),
    }


def _source_has_secret(source: DataSource, pending: dict[str, object]) -> bool:
    """数据源是否已有可用运行密钥（控制台密文或非生产环境兼容回退）。"""
    if pending.get("api_key_encrypted") or source.api_key_encrypted:
        return True
    return source.code == "tianyancha" and bool(config.get_tyc_env_fallback())


def _sanitize_login_config(config: dict[str, object] | None) -> dict[str, object]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "client_secret"}

    def clean(value: object, key: str | None = None) -> object:
        if key and key.lower() in sensitive:
            return "***"
        if isinstance(value, dict):
            return {
                str(child_key): clean(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(config) if config else {}  # type: ignore[return-value]


def _audit(
    session: Session,
    *,
    source_id: int | None,
    action: str,
    changes: dict[str, object],
    actor: User,
) -> None:
    session.add(
        DataSourceAuditLog(
            source_id=source_id,
            action=action,
            actor_role=actor.role,
            actor_id=str(actor.id),
            changes=changes,
        )
    )


def _serialize_source(source: DataSource, session: Session | None = None) -> DataSourceRead:
    payload = DataSourceRead.model_validate(source)
    effective_endpoint = source.endpoint_url
    if source.code == NmcWeatherAdapter.source_code:
        effective_endpoint = NmcWeatherAdapter.endpoint
    elif source.code == OfacSdnAdapter.source_code:
        effective_endpoint = OfacSdnAdapter.endpoint
    endpoint_host = (
        (urlparse(effective_endpoint).hostname or "").lower() if effective_endpoint else ""
    )
    access = session.get(SourceHostAccess, endpoint_host) if session and endpoint_host else None
    if access:
        now = datetime.now(UTC)
        access_status = "ready"
        if access.cooldown_until and access.cooldown_until > now:
            access_status = "cooldown"
        elif access.lease_until and access.lease_until > now:
            access_status = "busy"
        elif access.next_request_at and access.next_request_at > now:
            access_status = "throttled"
        payload = payload.model_copy(
            update={
                "access_status": access_status,
                "access_cooldown_until": access.cooldown_until,
                "access_last_http_status": access.last_http_status,
                "access_last_error_kind": access.last_error_kind,
                "endpoint_url": effective_endpoint,
            }
        )
    elif effective_endpoint != source.endpoint_url:
        payload = payload.model_copy(update={"endpoint_url": effective_endpoint})
    if source.code == "tianyancha":
        # 运行密钥优先取控制台加密存库；环境变量仅在非生产环境兼容回退
        db_configured = (
            source.api_key_encrypted is not None
            and decrypt_secret(source.api_key_encrypted) is not None
    )
        env_configured = bool(config.get_tyc_env_fallback())
        return payload.model_copy(
            update={
                "api_key_configured": db_configured or env_configured,
                "api_key_hint": (
                    payload.api_key_hint
                    if db_configured
                    else "环境变量已配置" if env_configured else None
                ),
            }
        )
    return payload


def _serialize_source_summary(
    source: DataSource, session: Session | None = None
) -> DataSourceSummaryRead:
    return DataSourceSummaryRead.model_validate(_serialize_source(source, session))


def build_pull_adapter(source: DataSource | str) -> PullSourceAdapter:
    """按数据源编码构建拉取式适配器；不支持时抛 SourceNotCollectable。"""
    source_code = source.code if isinstance(source, DataSource) else source
    if source_code == NmcWeatherAdapter.source_code:
        return NmcWeatherAdapter()
    if source_code == OfacSdnAdapter.source_code:
        return OfacSdnAdapter()
    if source_code == EuOfficialJournalAdapter.source_code:
        return EuOfficialJournalAdapter()
    if source_code == EuComplianceAdapter.source_code:
        return EuComplianceAdapter()
    if source_code == UflpaEntityAdapter.source_code:
        return UflpaEntityAdapter()
    if source_code == BisEntityListAdapter.source_code:
        return BisEntityListAdapter()
    if source_code == CommodityFuturesAdapter.source_code:
        return CommodityFuturesAdapter()
    if source_code == PbcLprAdapter.source_code:
        return PbcLprAdapter()
    if source_code == StatsPmiAdapter.source_code:
        return StatsPmiAdapter()
    if source_code == WtoNewsAdapter.source_code:
        return WtoNewsAdapter()
    if source_code == CustomsAnnouncementAdapter.source_code:
        return CustomsAnnouncementAdapter()
    if source_code == MofcomEntityDetailAdapter.source_code:
        return MofcomEntityDetailAdapter()
    if source_code == FxRatesAdapter.source_code:
        return FxRatesAdapter()
    if source_code == SseShippingAdapter.source_code:
        return SseShippingAdapter()
    if source_code == FmprcPressAdapter.source_code:
        return FmprcPressAdapter()
    if isinstance(source, DataSource) and source.adapter_status == "published":
        try:
            spec = AdapterSpec.model_validate(source.adapter_config)
        except ValidationError as exc:
            raise SourceNotCollectable(f"数据源 {source_code} 的适配器配置无效") from exc
        return DeclarativeSourceAdapter(
            source_code,
            spec,
            auth_type=source.auth_type,
            credential_ref=source.credential_ref,
            login_config=source.login_config,
        )
    raise SourceNotCollectable(f"数据源 {source_code} 不支持手动拉取采集")


def _adapter_spec(config: dict[str, object] | None) -> AdapterSpec | None:
    if config is None:
        return None
    try:
        return AdapterSpec.model_validate(config)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "适配器配置无效", "errors": exc.errors(include_url=False)},
        ) from exc


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


@router.get("/sources", response_model=list[DataSourceSummaryRead])
def list_sources(
    session: SessionDependency, _user: SourceStatusView
) -> list[DataSourceSummaryRead]:
    return [
        _serialize_source_summary(source, session)
        for source in session.scalars(select(DataSource).order_by(DataSource.id))
    ]


@router.get("/sources/admin", response_model=list[DataSourceRead])
def list_sources_admin(
    session: SessionDependency, _user: SourceManage
) -> list[DataSourceRead]:
    return [
        _serialize_source(source, session)
        for source in session.scalars(select(DataSource).order_by(DataSource.id))
    ]


@router.get("/sources/audit-logs", response_model=DataSourceAuditLogListResponse)
def list_source_audit_logs(
    session: SessionDependency,
    _user: BusinessAuditView,
    source_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DataSourceAuditLogListResponse:
    filters = [DataSourceAuditLog.source_id == source_id] if source_id is not None else []
    total = session.scalar(
        select(func.count()).select_from(DataSourceAuditLog).where(*filters)
    ) or 0
    items = list(
        session.scalars(
            select(DataSourceAuditLog)
            .where(*filters)
            .order_by(DataSourceAuditLog.created_at.desc(), DataSourceAuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return DataSourceAuditLogListResponse(
        items=[DataSourceAuditLogRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/sources", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: DataSourceWrite,
    session: SessionDependency,
    user: SourceManage,
    _csrf: CsrfGuard,
) -> DataSourceRead:
    _validate_schedule(payload.schedule)
    spec = _adapter_spec(payload.adapter_config)
    if payload.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="新数据源必须先保存并发布适配器，创建时不得启用",
        )
    if session.scalar(select(DataSource).where(DataSource.code == payload.code)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="数据源编码已存在")
    source = DataSource(
        code=payload.code,
        name=payload.name,
        source_type=payload.source_type,
        credibility=payload.credibility,
        schedule=payload.schedule,
        endpoint_url=(
            spec.request.url
            if spec
            else str(payload.endpoint_url) if payload.endpoint_url else None
        ),
        auth_type=payload.auth_type,
        login_config=_sanitize_login_config(payload.login_config),
        credential_ref=payload.credential_ref,
        description=payload.description,
        adapter_config=spec.model_dump(mode="json") if spec else {},
        adapter_status="draft" if spec else "unconfigured",
        enabled=payload.enabled,
        **_api_key_fields(payload.api_key),
    )
    session.add(source)
    session.flush()
    _audit(
        session,
        source_id=source.id,
        action="created",
        actor=user,
        changes={"code": source.code, "name": source.name, "source_type": source.source_type},
    )
    session.commit()
    session.refresh(source)
    return _serialize_source(source, session)


@router.put("/sources/{source_id}", response_model=DataSourceRead)
def update_source(
    source_id: int,
    payload: DataSourceUpdate,
    session: SessionDependency,
    user: SourceManage,
    _csrf: CsrfGuard,
) -> DataSourceRead:
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    _validate_schedule(payload.schedule)
    changes: dict[str, object] = {}
    update_values = payload.model_dump(exclude_unset=True, exclude={"api_key"})
    if "adapter_config" in update_values and update_values["adapter_config"] is not None:
        spec = _adapter_spec(update_values["adapter_config"])
        assert spec is not None
        update_values["adapter_config"] = spec.model_dump(mode="json")
        update_values["endpoint_url"] = spec.request.url
        update_values["adapter_status"] = "draft"
        update_values["adapter_published_at"] = None
        update_values["enabled"] = False
        changes["adapter_config"] = "updated"
    elif update_values.get("adapter_config") is None:
        update_values.pop("adapter_config", None)
    requested_enabled = update_values.get("enabled")
    resulting_status = str(update_values.get("adapter_status") or source.adapter_status)
    is_external_tool = (
        str(update_values.get("source_type") or source.source_type) == "external_tool"
    )
    if requested_enabled is True:
        if is_external_tool:
            # 按需外部核查工具：不要求适配器发布，但必须已有运行密钥
            if not _source_has_secret(source, update_values):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="未配置运行密钥，不能启用",
                )
        elif resulting_status not in {"builtin", "published"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="数据源适配器尚未发布，不能启用",
            )
    if "login_config" in update_values:
        update_values["login_config"] = _sanitize_login_config(update_values["login_config"])
    if "endpoint_url" in update_values and update_values["endpoint_url"] is not None:
        update_values["endpoint_url"] = str(update_values["endpoint_url"])
    if "api_key" in payload.model_fields_set:
        update_values.update(
            _api_key_fields(payload.api_key)
            if payload.api_key
            else {
                "api_key_hash": None,
                "api_key_last4": None,
                "api_key_encrypted": None,
            }
        )
        changes["api_key"] = "updated" if payload.api_key else "cleared"
    for key, value in update_values.items():
        if key == "api_key":
            continue
        if getattr(source, key) != value:
            # adapter_config 以标记形式记录；密文只落库不进审计日志
            if key not in {"adapter_config", "api_key_encrypted"}:
                changes[key] = value
            setattr(source, key, value)
    if not changes:
        return _serialize_source(source, session)
    _audit(session, source_id=source.id, action="updated", actor=user, changes=changes)
    session.commit()
    session.refresh(source)
    return _serialize_source(source, session)


@router.post("/sources/preview", response_model=AdapterPreviewResponse)
async def preview_source_adapter(
    payload: AdapterPreviewRequest,
    _user: SourceManage,
    _csrf: CsrfGuard,
) -> AdapterPreviewResponse:
    spec = _adapter_spec(payload.adapter_config)
    assert spec is not None
    try:
        preview = await preview_adapter(
            payload.source_code,
            spec,
            auth_type=payload.auth_type,
            credential_ref=payload.credential_ref,
            login_config=_sanitize_login_config(payload.login_config),
        )
    except SourceFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"实时预览失败: {exc}",
        ) from exc
    return AdapterPreviewResponse(fetched_count=preview.fetched_count, items=preview.items)


@router.post("/sources/{source_id}/publish", response_model=DataSourceRead)
async def publish_source_adapter(
    source_id: int,
    session: SessionDependency,
    user: SourceManage,
    _csrf: CsrfGuard,
) -> DataSourceRead:
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    spec = _adapter_spec(source.adapter_config)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="数据源没有适配器草稿")
    try:
        await preview_adapter(
            source.code,
            spec,
            auth_type=source.auth_type,
            credential_ref=source.credential_ref,
            login_config=source.login_config,
        )
    except SourceFetchError as exc:
        if not exc.error_kind:
            source.adapter_status = "invalid"
            source.enabled = False
        _audit(
            session,
            source_id=source.id,
            action=("adapter_access_blocked" if exc.error_kind else "adapter_validation_failed"),
            actor=user,
            changes={"message": str(exc)[:500], "error_kind": exc.error_kind},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"发布前实时验证失败: {exc}",
        ) from exc
    source.adapter_status = "published"
    source.adapter_version += 1
    source.adapter_published_at = datetime.now(UTC)
    source.enabled = False
    _audit(
        session,
        source_id=source.id,
        action="adapter_published",
        actor=user,
        changes={"adapter_version": source.adapter_version, "enabled": False},
    )
    session.commit()
    session.refresh(source)
    return _serialize_source(source, session)


@router.delete("/sources/{source_id}", response_model=dict[str, object])
def delete_source(
    source_id: int,
    session: SessionDependency,
    user: SourceManage,
    _csrf: CsrfGuard,
) -> dict[str, object]:
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
    has_history = bool(
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(CollectionRun.source_id == source_id)
        )
        or session.scalar(
            select(func.count()).select_from(RawSignal).where(RawSignal.source_id == source_id)
        )
    )
    if has_history:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="数据源已有采集或风险信号历史，请改为停用以保留证据链",
        )
    code = source.code
    _audit(
        session,
        source_id=source.id,
        action="deleted",
        actor=user,
        changes={"code": code},
    )
    session.delete(source)
    session.commit()
    return {"deleted": True, "id": source_id, "code": code}


@router.get("/collection-runs", response_model=CollectionRunListResponse)
def list_collection_runs(
    session: SessionDependency,
    _user: SourceStatusView,
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
    _user: SignalImport,
    _csrf: CsrfGuard,
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
    source_id: int,
    session: SessionDependency,
    _user: CollectionTrigger,
    _csrf: CsrfGuard,
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
        pull_adapter = build_pull_adapter(source)
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
