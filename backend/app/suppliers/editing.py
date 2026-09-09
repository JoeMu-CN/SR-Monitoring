"""供应商 PUT 的并发版本保护与子表受控同步。

仅网页编辑（携带子表 id 与 expected_updated_at 的 PUT）走本模块；
Excel 导入与新增继续使用 router.replace_supplier_details 的全量替换合同。

- lock：SELECT ... FOR UPDATE 锁定父行，阻止两个会话基于同一版本并发写入；
  锁获取后读到的 updated_at 必然是最新已提交版本。
- assert：expected_updated_at 统一规范化为 UTC aware 后比较；naive 输入按 UTC 处理。
- sync：按子项 id 受控同步。有 id 且存在的子项原位更新（同一 ORM 对象）；
  无 id 的子项新增；payload 未引用的已有子项删除。未变化行不 DELETE/INSERT。
"""
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct, SupplierSite
from app.suppliers.queries import supplier_options
from app.suppliers.schemas import (
    AliasInput,
    ProductInput,
    SiteInput,
    SupplierUpdate,
    normalize_alias,
)


def lock_supplier_for_update(session: Session, supplier_id: int) -> Supplier:
    supplier = session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .options(*supplier_options())
        .with_for_update()
    )
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="供应商不存在")
    return supplier


def assert_version_matches(
    supplier: Supplier, expected_updated_at: datetime | None
) -> None:
    if expected_updated_at is None:
        return
    if _as_utc(supplier.updated_at) != _as_utc(expected_updated_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "supplier_changed",
                "message": "供应商已被其他人修改，请刷新后重试",
            },
        )


def apply_supplier_update(supplier: Supplier, payload: SupplierUpdate) -> Supplier:
    supplier.legal_name = payload.legal_name
    supplier.country_code = payload.country_code
    supplier.registry_no = payload.registry_no
    supplier.registration_address = payload.registration_address
    supplier.industry = payload.industry
    supplier.raw_materials = list(payload.raw_materials)
    supplier.enabled = payload.enabled
    supplier.updated_at = datetime.now(UTC)
    _sync_aliases(supplier, payload.aliases)
    _sync_sites(supplier, payload.sites)
    _sync_products(supplier, payload.products)
    return supplier


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sync_aliases(supplier: Supplier, items: list[AliasInput]) -> None:
    by_id = {alias.id: alias for alias in supplier.aliases if alias.id is not None}
    by_name = {alias.normalized_alias: alias for alias in supplier.aliases}
    ordered: list[SupplierAlias] = []
    for item in items:
        normalized = normalize_alias(item.alias)
        target = by_id.get(item.id) if item.id is not None else None
        # 未携带 id 的旧客户端按规范化名称回退匹配，保持既有行 id 不变。
        if target is None and item.id is None:
            target = by_name.get(normalized)
        if target is None:
            target = SupplierAlias(
                alias=item.alias,
                language=item.language,
                normalized_alias=normalized,
            )
        else:
            target.alias = item.alias
            target.language = item.language
            target.normalized_alias = normalized
        ordered.append(target)
    supplier.aliases = ordered


def _sync_sites(supplier: Supplier, items: list[SiteInput]) -> None:
    by_id = {site.id: site for site in supplier.sites if site.id is not None}
    by_name = {site.site_name: site for site in supplier.sites}
    ordered: list[SupplierSite] = []
    for item in items:
        target = by_id.get(item.id) if item.id is not None else None
        # 未携带 id 的旧客户端按地点名称回退匹配，保持既有行 id 不变。
        if target is None and item.id is None:
            target = by_name.get(item.site_name)
        if target is None:
            target = SupplierSite(
                site_name=item.site_name,
                country_code=item.country_code,
                region=item.region,
                city=item.city,
                district=item.district,
                address=item.address,
                latitude=item.latitude,
                longitude=item.longitude,
            )
        else:
            target.site_name = item.site_name
            target.country_code = item.country_code
            target.region = item.region
            target.city = item.city
            target.district = item.district
            target.address = item.address
            target.latitude = item.latitude
            target.longitude = item.longitude
        ordered.append(target)
    supplier.sites = ordered


def _sync_products(supplier: Supplier, items: list[ProductInput]) -> None:
    by_id = {product.id: product for product in supplier.products if product.id is not None}
    by_name = {product.name: product for product in supplier.products}
    ordered: list[SupplierProduct] = []
    for item in items:
        target = by_id.get(item.id) if item.id is not None else None
        # 未携带 id 的旧客户端按产品名称回退匹配，保持既有行 id 不变。
        if target is None and item.id is None:
            target = by_name.get(item.name)
        if target is None:
            target = SupplierProduct(name=item.name, keywords=list(item.keywords))
        else:
            target.name = item.name
            target.keywords = list(item.keywords)
        ordered.append(target)
    supplier.products = ordered
