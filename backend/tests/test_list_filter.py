"""清单类信源供应商名预筛测试（_matches_any_supplier + _process_pending_signals）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.scheduler.jobs import _matches_any_supplier
from app.signals.relevance import _LIST_SOURCE_CODES
from app.suppliers.models import Supplier, SupplierAlias


def _add_supplier(
    session: Session,
    code: str,
    name: str,
    aliases: tuple[str, ...] = (),
) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=name,
        country_code="CN",
        registry_no=f"REG-{code}",
        enabled=True,
    )
    session.add(supplier)
    session.flush()
    for alias in aliases:
        session.add(
            SupplierAlias(
                supplier_id=supplier.id,
                alias=alias,
                normalized_alias=" ".join(alias.casefold().split()),
            )
        )
    session.flush()
    return supplier


def test_list_source_codes_covers_entity_lists() -> None:
    """清单类信源集合覆盖全部全量实体清单信源。"""
    assert {
        "ofac-sdn",
        "bis-entity-list",
        "uflpa-entity-list",
        "mofcom-entity-detail",
        "mofcom-entity-control",
    } == _LIST_SOURCE_CODES


def test_matches_supplier_legal_name(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-1", "苏州欣科导轨有限公司")
    assert _matches_any_supplier(
        db_session, "苏州欣科导轨有限公司, No. 88 Road, China"
    ) is True
    assert _matches_any_supplier(db_session, "Baoding LYSZD Trade Co.") is False


def test_matches_supplier_alias(db_session: Session) -> None:
    _add_supplier(db_session, "SUP-2", "宁波鸿腾精密制造有限公司", aliases=("鸿腾精密",))
    assert _matches_any_supplier(db_session, "鸿腾精密涉及出口管制") is True
    assert _matches_any_supplier(db_session, "无关公司名称") is False


def test_disabled_supplier_not_matched(db_session: Session) -> None:
    supplier = _add_supplier(db_session, "SUP-3", "苏州欣科导轨有限公司")
    supplier.enabled = False
    db_session.flush()
    assert _matches_any_supplier(db_session, "苏州欣科导轨有限公司") is False
