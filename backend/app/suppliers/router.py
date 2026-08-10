from datetime import UTC, datetime
from io import BytesIO
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.interfaces import ORMOption
from sqlalchemy.sql.elements import ColumnElement

from app.database import get_session
from app.security import require_admin
from app.suppliers.importer import (
    MAX_FILE_BYTES,
    WorkbookValidationError,
    create_template,
    parse_workbook,
)
from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct, SupplierSite
from app.suppliers.schemas import (
    COUNTRY_CODE_PATTERN,
    EnabledUpdate,
    ImportSummary,
    SupplierCreate,
    SupplierListResponse,
    SupplierRead,
    SupplierUpdate,
    normalize_alias,
)

router = APIRouter(prefix="/api/v1/suppliers", tags=["供应商"])
SessionDependency = Annotated[Session, Depends(get_session)]


def supplier_options() -> tuple[ORMOption, ORMOption, ORMOption]:
    return (
        selectinload(Supplier.aliases),
        selectinload(Supplier.sites),
        selectinload(Supplier.products),
    )


def get_supplier_or_404(session: Session, supplier_id: int) -> Supplier:
    supplier = session.scalar(
        select(Supplier).where(Supplier.id == supplier_id).options(*supplier_options())
    )
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="供应商不存在")
    return supplier


def replace_supplier_details(
    session: Session, supplier: Supplier, payload: SupplierCreate | SupplierUpdate
) -> None:
    if supplier.id is not None:
        supplier.aliases.clear()
        supplier.sites.clear()
        supplier.products.clear()
        session.flush()

    supplier.legal_name = payload.legal_name
    supplier.country_code = payload.country_code
    supplier.registry_no = payload.registry_no
    supplier.industry = payload.industry
    supplier.raw_materials = list(payload.raw_materials)
    supplier.enabled = payload.enabled
    supplier.updated_at = datetime.now(UTC)
    supplier.aliases = [
        SupplierAlias(
            alias=item.alias,
            language=item.language,
            normalized_alias=normalize_alias(item.alias),
        )
        for item in payload.aliases
    ]
    supplier.sites = [
        SupplierSite(
            site_name=item.site_name,
            country_code=item.country_code,
            region=item.region,
            city=item.city,
            address=item.address,
            latitude=item.latitude,
            longitude=item.longitude,
        )
        for item in payload.sites
    ]
    supplier.products = [
        SupplierProduct(name=item.name, keywords=item.keywords) for item in payload.products
    ]


def commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="供应商编码、注册编号、地点或产品与已有数据冲突",
        ) from exc


@router.get("/import-template")
def download_import_template() -> StreamingResponse:
    filename = quote("供应商导入模板.xlsx")
    return StreamingResponse(
        BytesIO(create_template()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/import", response_model=ImportSummary)
async def import_suppliers(
    session: SessionDependency,
    file: Annotated[UploadFile, File(description="标准供应商 .xlsx 文件")],
) -> ImportSummary:
    filename = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"errors": [{"sheet": "工作簿", "message": "只支持 .xlsx 文件"}]},
        )

    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        plan = parse_workbook(data)
    except WorkbookValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"errors": [issue.model_dump() for issue in exc.issues]},
        ) from exc

    codes = [item.supplier_code for item in plan.suppliers]
    existing = {
        item.supplier_code: item
        for item in session.scalars(
            select(Supplier)
            .where(Supplier.supplier_code.in_(codes))
            .options(*supplier_options())
        ).unique()
    }

    created = 0
    updated = 0
    for payload in plan.suppliers:
        supplier = existing.get(payload.supplier_code)
        if supplier is None:
            supplier = Supplier(supplier_code=payload.supplier_code)
            session.add(supplier)
            created += 1
        else:
            updated += 1
        replace_supplier_details(session, supplier, payload)

    commit_or_conflict(session)
    return ImportSummary(
        created_suppliers=created,
        updated_suppliers=updated,
        aliases=sum(len(item.aliases) for item in plan.suppliers),
        sites=sum(len(item.sites) for item in plan.suppliers),
        products=sum(len(item.products) for item in plan.suppliers),
    )


@router.get("", response_model=SupplierListResponse)
def list_suppliers(
    session: SessionDependency,
    q: Annotated[str | None, Query(max_length=100)] = None,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    enabled: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SupplierListResponse:
    filters: list[ColumnElement[bool]] = []
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                Supplier.supplier_code.ilike(pattern, escape="\\"),
                Supplier.legal_name.ilike(pattern, escape="\\"),
                Supplier.aliases.any(SupplierAlias.alias.ilike(pattern, escape="\\")),
            )
        )
    if country_code:
        normalized_country = country_code.upper()
        if not COUNTRY_CODE_PATTERN.fullmatch(normalized_country):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="国家代码必须是两个英文字母",
            )
        filters.append(Supplier.country_code == normalized_country)
    if enabled is not None:
        filters.append(Supplier.enabled == enabled)

    total = session.scalar(select(func.count()).select_from(Supplier).where(*filters)) or 0
    items = list(
        session.scalars(
            select(Supplier)
            .where(*filters)
            .options(*supplier_options())
            .order_by(Supplier.updated_at.desc(), Supplier.id.desc())
            .limit(limit)
            .offset(offset)
        ).unique()
    )
    return SupplierListResponse(
        items=[SupplierRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
def create_supplier(payload: SupplierCreate, session: SessionDependency) -> Supplier:
    supplier = Supplier(supplier_code=payload.supplier_code)
    replace_supplier_details(session, supplier, payload)
    session.add(supplier)
    commit_or_conflict(session)
    return get_supplier_or_404(session, supplier.id)


@router.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(supplier_id: int, session: SessionDependency) -> Supplier:
    return get_supplier_or_404(session, supplier_id)


@router.put("/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: int, payload: SupplierUpdate, session: SessionDependency
) -> Supplier:
    supplier = get_supplier_or_404(session, supplier_id)
    replace_supplier_details(session, supplier, payload)
    commit_or_conflict(session)
    return get_supplier_or_404(session, supplier.id)


@router.patch("/{supplier_id}/enabled", response_model=SupplierRead)
def update_supplier_enabled(
    supplier_id: int,
    payload: EnabledUpdate,
    session: SessionDependency,
    _admin: Annotated[str, Depends(require_admin)],
) -> Supplier:
    supplier = get_supplier_or_404(session, supplier_id)
    supplier.enabled = payload.enabled
    supplier.updated_at = datetime.now(UTC)
    commit_or_conflict(session)
    return get_supplier_or_404(session, supplier.id)
