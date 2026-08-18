import re
import unicodedata
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SupplierCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]

COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")


def normalize_alias(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


class AliasInput(BaseModel):
    alias: NonEmptyText
    language: str | None = None

    @field_validator("language", mode="before")
    @classmethod
    def clean_language(cls, value: object) -> str | None:
        return clean_optional_text(value)


class SiteInput(BaseModel):
    site_name: NonEmptyText
    country_code: str
    region: str | None = None
    city: str | None = None
    district: str | None = None
    address: NonEmptyText
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not COUNTRY_CODE_PATTERN.fullmatch(normalized):
            raise ValueError("国家代码必须是两个英文字母")
        return normalized

    @field_validator("region", "city", "district", mode="before")
    @classmethod
    def clean_optional_fields(cls, value: object) -> str | None:
        return clean_optional_text(value)

    @model_validator(mode="after")
    def validate_coordinate_pair(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("纬度和经度必须同时填写或同时留空")
        return self


class ProductInput(BaseModel):
    name: NonEmptyText
    keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class SupplierBase(BaseModel):
    legal_name: NonEmptyText
    country_code: str
    registry_no: str | None = None
    registration_address: str | None = None
    industry: str | None = None
    raw_materials: list[str] = Field(default_factory=list)
    enabled: bool = True
    aliases: list[AliasInput] = Field(default_factory=list)
    sites: list[SiteInput] = Field(default_factory=list)
    products: list[ProductInput] = Field(default_factory=list)

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not COUNTRY_CODE_PATTERN.fullmatch(normalized):
            raise ValueError("国家代码必须是两个英文字母")
        return normalized

    @field_validator("registry_no", mode="before")
    @classmethod
    def clean_registry_no(cls, value: object) -> str | None:
        return clean_optional_text(value)

    @field_validator("registration_address", mode="before")
    @classmethod
    def clean_registration_address(cls, value: object) -> str | None:
        return clean_optional_text(value)

    @field_validator("industry", mode="before")
    @classmethod
    def clean_industry(cls, value: object) -> str | None:
        return clean_optional_text(value)

    @field_validator("raw_materials", mode="before")
    @classmethod
    def clean_raw_materials(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return list(dict.fromkeys(item.strip() for item in value if str(item).strip()))
        if isinstance(value, str):
            return [
                item.strip()
                for item in value.replace("；", ";").split(";")
                if item.strip()
            ]
        return []

    @model_validator(mode="after")
    def remove_duplicate_children(self) -> Self:
        aliases: dict[str, AliasInput] = {}
        for alias_item in self.aliases:
            aliases.setdefault(normalize_alias(alias_item.alias), alias_item)
        self.aliases = list(aliases.values())

        sites: dict[str, SiteInput] = {}
        for site_item in self.sites:
            sites.setdefault(normalize_alias(site_item.site_name), site_item)
        self.sites = list(sites.values())

        products: dict[str, ProductInput] = {}
        for product_item in self.products:
            products.setdefault(normalize_alias(product_item.name), product_item)
        self.products = list(products.values())
        return self


class SupplierCreate(SupplierBase):
    supplier_code: SupplierCode


class SupplierUpdate(SupplierBase):
    pass


class EnabledUpdate(BaseModel):
    enabled: bool


class AliasRead(AliasInput):
    model_config = ConfigDict(from_attributes=True)

    id: int


class SiteRead(SiteInput):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ProductRead(ProductInput):
    model_config = ConfigDict(from_attributes=True)

    id: int


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_code: str
    legal_name: str
    country_code: str
    registry_no: str | None
    registration_address: str | None
    industry: str | None
    raw_materials: list[str]
    enabled: bool
    aliases: list[AliasRead]
    sites: list[SiteRead]
    products: list[ProductRead]


class SupplierListResponse(BaseModel):
    items: list[SupplierRead]
    total: int
    limit: int
    offset: int


class ImportIssue(BaseModel):
    sheet: str
    row: int | None = None
    field: str | None = None
    message: str


class ImportSummary(BaseModel):
    created_suppliers: int
    updated_suppliers: int
    aliases: int
    sites: int
    products: int
