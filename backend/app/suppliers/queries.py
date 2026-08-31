from dataclasses import dataclass

from sqlalchemy import case, func, or_, select, true
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.interfaces import ORMOption
from sqlalchemy.sql.elements import ColumnElement

from app.risks.models import RiskAlert, SupplierEventMatch
from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct
from app.suppliers.schemas import (
    SupplierListItem,
    SupplierListResponse,
    SupplierRead,
    normalize_alias,
)


@dataclass(frozen=True, slots=True)
class SupplierListQuery:
    q: str | None
    country_code: str | None
    enabled: bool | None
    has_current_alert: bool | None
    limit: int
    offset: int


def supplier_options() -> tuple[ORMOption, ORMOption, ORMOption]:
    return (
        selectinload(Supplier.aliases),
        selectinload(Supplier.sites),
        selectinload(Supplier.products),
    )


def find_supplier(session: Session, supplier_id: int) -> Supplier | None:
    return session.scalar(
        select(Supplier).where(Supplier.id == supplier_id).options(*supplier_options())
    )


def list_suppliers(session: Session, query: SupplierListQuery) -> SupplierListResponse:
    level_strength = case(
        (RiskAlert.level == "P1", 1),
        (RiskAlert.level == "P2", 2),
        (RiskAlert.level == "P3", 3),
        (RiskAlert.level == "P4", 4),
        else_=5,
    )
    ranked_alerts = (
        select(
            SupplierEventMatch.supplier_id.label("supplier_id"),
            RiskAlert.level.label("current_risk_level"),
            RiskAlert.score.label("current_risk_score"),
            func.row_number()
            .over(
                partition_by=SupplierEventMatch.supplier_id,
                order_by=(level_strength, RiskAlert.score.desc(), RiskAlert.id.asc()),
            )
            .label("risk_rank"),
        )
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .where(
            RiskAlert.status == "current",
            or_(RiskAlert.expires_at.is_(None), RiskAlert.expires_at > func.now()),
        )
        .subquery()
    )
    strongest_alert = (
        select(
            ranked_alerts.c.supplier_id,
            ranked_alerts.c.current_risk_level,
            ranked_alerts.c.current_risk_score,
        )
        .where(ranked_alerts.c.risk_rank == 1)
        .subquery()
    )
    filters: list[ColumnElement[bool]] = []
    normalized_query = query.q.strip() if query.q else ""
    if normalized_query:
        escaped = (
            normalized_query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        normalized_alias_query = normalize_alias(normalized_query)
        escaped_normalized_alias = (
            normalized_alias_query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        normalized_alias_pattern = f"%{escaped_normalized_alias}%"
        keyword_values = func.jsonb_array_elements_text(
            SupplierProduct.keywords
        ).table_valued("value")
        keyword_match = (
            select(1)
            .select_from(SupplierProduct)
            .join(keyword_values, true())
            .where(
                SupplierProduct.supplier_id == Supplier.id,
                keyword_values.c.value.ilike(pattern, escape="\\"),
            )
            .exists()
        )
        filters.append(
            or_(
                Supplier.supplier_code.ilike(pattern, escape="\\"),
                Supplier.legal_name.ilike(pattern, escape="\\"),
                Supplier.registry_no.ilike(pattern, escape="\\"),
                Supplier.aliases.any(
                    or_(
                        SupplierAlias.alias.ilike(pattern, escape="\\"),
                        SupplierAlias.normalized_alias.ilike(
                            normalized_alias_pattern, escape="\\"
                        ),
                    )
                ),
                Supplier.products.any(
                    SupplierProduct.name.ilike(pattern, escape="\\")
                ),
                keyword_match,
            )
        )
    if query.country_code:
        filters.append(Supplier.country_code == query.country_code)
    if query.enabled is not None:
        filters.append(Supplier.enabled == query.enabled)
    if query.has_current_alert is not None:
        filters.append(
            strongest_alert.c.supplier_id.is_not(None)
            if query.has_current_alert
            else strongest_alert.c.supplier_id.is_(None)
        )

    total = (
        session.scalar(
            select(func.count())
            .select_from(Supplier)
            .outerjoin(
                strongest_alert, strongest_alert.c.supplier_id == Supplier.id
            )
            .where(*filters)
        )
        or 0
    )
    rows = session.execute(
        select(
            Supplier,
            strongest_alert.c.current_risk_level,
            strongest_alert.c.current_risk_score,
        )
        .outerjoin(strongest_alert, strongest_alert.c.supplier_id == Supplier.id)
        .where(*filters)
        .options(*supplier_options())
        .order_by(Supplier.supplier_code.asc(), Supplier.id.asc())
        .limit(query.limit)
        .offset(query.offset)
    ).unique()
    items = [
        SupplierListItem(
            **SupplierRead.model_validate(supplier).model_dump(),
            current_risk_level=current_risk_level,
            current_risk_score=current_risk_score,
        )
        for supplier, current_risk_level, current_risk_score in rows
    ]
    return SupplierListResponse(
        items=items,
        total=total,
        limit=query.limit,
        offset=query.offset,
    )
