from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_suppliers_country_code"),
        Index(
            "uq_suppliers_country_registry",
            "country_code",
            "registry_no",
            unique=True,
            postgresql_where=text("registry_no IS NOT NULL"),
        ),
        Index("ix_suppliers_enabled_updated_at", "enabled", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_code: Mapped[str] = mapped_column(Text, unique=True)
    legal_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(Text)
    registry_no: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    aliases: Mapped[list["SupplierAlias"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", passive_deletes=True
    )
    sites: Mapped[list["SupplierSite"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", passive_deletes=True
    )
    products: Mapped[list["SupplierProduct"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", passive_deletes=True
    )


class SupplierAlias(Base):
    __tablename__ = "supplier_aliases"
    __table_args__ = (
        UniqueConstraint("supplier_id", "normalized_alias", name="uq_supplier_aliases_value"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE")
    )
    alias: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    normalized_alias: Mapped[str] = mapped_column(Text)

    supplier: Mapped[Supplier] = relationship(back_populates="aliases")


class SupplierSite(Base):
    __tablename__ = "supplier_sites"
    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_supplier_sites_country_code"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_supplier_sites_coordinate_pair",
        ),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="ck_supplier_sites_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="ck_supplier_sites_longitude",
        ),
        UniqueConstraint("supplier_id", "site_name", name="uq_supplier_sites_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE")
    )
    site_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    supplier: Mapped[Supplier] = relationship(back_populates="sites")


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "name", name="uq_supplier_products_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    supplier: Mapped[Supplier] = relationship(back_populates="products")
