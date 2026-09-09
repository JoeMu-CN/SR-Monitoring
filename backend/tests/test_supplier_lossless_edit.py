"""供应商 PUT 无损编辑、受控子表同步与并发版本保护的真实 PostgreSQL 集成测试。

验收点：
- 2地点/2产品/别名/坐标/关键词且 enabled=false，仅改名称：其余字段与子项 id 逐项相等。
- 改首产品名保留 keywords；改首地点地址保留 country/coordinates/其他字段。
- 受控同步：无变化的子表行不 DELETE/INSERT（白盒对象身份 + 行 id 稳定）；
  新增/删除只影响对应项。
- 两个真实 Session 基于同一版本先后提交：第二个收到 409 supplier_changed。
- 等价时间点不同 UTC 偏移的令牌不误判；未携带令牌的旧 PUT 兼容。
- 401/403 不泄露详情；中途约束失败整事务回滚。
"""
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.main import app
from app.suppliers.editing import (
    apply_supplier_update,
    assert_version_matches,
    lock_supplier_for_update,
)
from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct, SupplierSite
from app.suppliers.schemas import (
    AliasInput,
    ProductInput,
    SiteInput,
    SupplierUpdate,
    normalize_alias,
)

CONFLICT_CODE = "supplier_changed"


def _full_create_payload(code: str) -> dict[str, Any]:
    return {
        "supplier_code": code,
        "legal_name": "无损编辑测试有限公司",
        "country_code": "CN",
        "registry_no": f"91310000{uuid4().hex[:10].upper()}",
        "registration_address": "上海市登记路1号",
        "industry": "精密制造",
        "raw_materials": ["铝材", "铜材"],
        "enabled": False,
        "aliases": [
            {"alias": "无损测试", "language": "zh"},
            {"alias": "Lossless Test", "language": "en"},
        ],
        "sites": [
            {
                "site_name": "上海工厂",
                "country_code": "DE",
                "region": "上海地区",
                "city": "上海市",
                "district": "浦东新区",
                "address": "浦东生产路1号",
                "latitude": 31.2304,
                "longitude": 121.4737,
            },
            {
                "site_name": "深圳工厂",
                "country_code": "CN",
                "region": "广东省",
                "city": "深圳市",
                "district": "南山区",
                "address": "南山生产路2号",
                "latitude": 22.5431,
                "longitude": 114.0579,
            },
        ],
        "products": [
            {"name": "精密轴承", "keywords": ["轴承", "bearing"]},
            {"name": "齿轮箱", "keywords": ["齿轮"]},
        ],
    }


def _create_full_supplier(client: TestClient, code: str) -> dict[str, Any]:
    response = client.post("/api/v1/suppliers", json=_full_create_payload(code))
    assert response.status_code == 201, response.text
    detail = client.get(f"/api/v1/suppliers/{response.json()['id']}").json()
    assert len(detail["sites"]) == 2
    assert len(detail["products"]) == 2
    return detail


def _put_body(detail: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {key: value for key, value in detail.items() if key not in {"id", "supplier_code"}}
    body["expected_updated_at"] = detail["updated_at"]
    body.update(overrides)
    return body


def _child_by_id(children: list[dict[str, Any]], child_id: int) -> dict[str, Any]:
    return next(child for child in children if child["id"] == child_id)


# ---------------------------------------------------------------- HTTP 行为


def test_edit_only_legal_name_keeps_everything_else_and_child_ids(
    client: TestClient,
) -> None:
    detail = _create_full_supplier(client, "SUP-LOSSLESS-1")
    supplier_id = detail["id"]

    response = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(detail, legal_name="无损编辑测试股份公司"),
    )

    assert response.status_code == 200, response.text
    after = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    assert after["legal_name"] == "无损编辑测试股份公司"
    for field in (
        "country_code",
        "registry_no",
        "registration_address",
        "industry",
        "raw_materials",
    ):
        assert after[field] == detail[field], field
    assert after["enabled"] is False
    assert after["updated_at"] != detail["updated_at"]

    before_aliases = sorted(detail["aliases"], key=lambda item: item["id"])
    after_aliases = sorted(after["aliases"], key=lambda item: item["id"])
    assert [item["id"] for item in after_aliases] == [item["id"] for item in before_aliases]
    assert after_aliases == before_aliases

    before_sites = sorted(detail["sites"], key=lambda item: item["id"])
    after_sites = sorted(after["sites"], key=lambda item: item["id"])
    assert [item["id"] for item in after_sites] == [item["id"] for item in before_sites]
    assert after_sites == before_sites

    before_products = sorted(detail["products"], key=lambda item: item["id"])
    after_products = sorted(after["products"], key=lambda item: item["id"])
    assert [item["id"] for item in after_products] == [
        item["id"] for item in before_products
    ]
    assert after_products == before_products


def test_edit_first_product_name_keeps_keywords(client: TestClient) -> None:
    detail = _create_full_supplier(client, "SUP-PRODUCT-KW-1")
    supplier_id = detail["id"]
    first_product = detail["products"][0]

    response = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(
            detail,
            products=[
                {**first_product, "name": "高精度轴承"},
                detail["products"][1],
            ],
        ),
    )

    assert response.status_code == 200, response.text
    after = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    renamed = _child_by_id(after["products"], first_product["id"])
    assert renamed["name"] == "高精度轴承"
    assert renamed["keywords"] == first_product["keywords"]
    kept = _child_by_id(after["products"], detail["products"][1]["id"])
    assert kept == detail["products"][1]
    assert {site["id"] for site in after["sites"]} == {
        site["id"] for site in detail["sites"]
    }


def test_edit_first_site_address_keeps_country_coordinates_and_others(
    client: TestClient,
) -> None:
    detail = _create_full_supplier(client, "SUP-SITE-LOSSLESS-1")
    supplier_id = detail["id"]
    first_site = detail["sites"][0]

    response = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(
            detail,
            sites=[{**first_site, "address": "浦东新生产路99号"}, detail["sites"][1]],
        ),
    )

    assert response.status_code == 200, response.text
    after = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    edited = _child_by_id(after["sites"], first_site["id"])
    assert edited["address"] == "浦东新生产路99号"
    for field in (
        "site_name",
        "country_code",
        "region",
        "city",
        "district",
        "latitude",
        "longitude",
    ):
        assert edited[field] == first_site[field], field
    assert _child_by_id(after["sites"], detail["sites"][1]["id"]) == detail["sites"][1]
    after_products = sorted(after["products"], key=lambda item: item["id"])
    before_products = sorted(detail["products"], key=lambda item: item["id"])
    assert after_products == before_products


def test_put_without_expected_updated_at_keeps_working(client: TestClient) -> None:
    detail = _create_full_supplier(client, "SUP-NO-TOKEN-1")
    supplier_id = detail["id"]
    body = {key: value for key, value in detail.items() if key not in {"id", "supplier_code"}}
    body["legal_name"] = "旧客户端兼容有限公司"

    response = client.put(f"/api/v1/suppliers/{supplier_id}", json=body)

    assert response.status_code == 200, response.text
    after = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    assert after["legal_name"] == "旧客户端兼容有限公司"
    assert len(after["sites"]) == 2


def test_equivalent_instant_with_different_offset_is_not_rejected(
    client: TestClient,
) -> None:
    detail = _create_full_supplier(client, "SUP-TZ-TOKEN-1")
    supplier_id = detail["id"]
    first = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(detail, industry="精密制造与装配"),
    )
    assert first.status_code == 200, first.text

    current = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    db_instant = datetime.fromisoformat(current["updated_at"])
    assert db_instant.tzinfo is not None
    plus_eight = db_instant.astimezone(timezone(timedelta(hours=8))).isoformat()

    second = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(current, expected_updated_at=plus_eight, industry="精密制造与装配二厂"),
    )
    assert second.status_code == 200, second.text


def test_controlled_remove_and_add_affect_only_targeted_children(
    client: TestClient,
) -> None:
    detail = _create_full_supplier(client, "SUP-CONTROLLED-1")
    supplier_id = detail["id"]
    kept_product = detail["products"][0]
    dropped_product = detail["products"][1]

    # 删除第二产品：只影响该子项，其余子表 id 全部稳定
    remove = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(detail, products=[kept_product]),
    )
    assert remove.status_code == 200, remove.text
    after_remove = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    assert [product["id"] for product in after_remove["products"]] == [kept_product["id"]]
    assert {site["id"] for site in after_remove["sites"]} == {
        site["id"] for site in detail["sites"]
    }
    assert {alias["id"] for alias in after_remove["aliases"]} == {
        alias["id"] for alias in detail["aliases"]
    }

    # 新增产品（无 id）：追加新行，既有行 id 不动
    add = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(
            after_remove,
            products=[kept_product, {"name": "新追加产品", "keywords": ["新增"]}],
        ),
    )
    assert add.status_code == 200, add.text
    after_add = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    assert [product["id"] for product in after_add["products"][:1]] == [kept_product["id"]]
    new_ids = {product["id"] for product in after_add["products"]}
    assert len(new_ids) == 2 and dropped_product["id"] not in new_ids
    assert {site["id"] for site in after_add["sites"]} == {
        site["id"] for site in detail["sites"]
    }


def test_integrity_failure_mid_put_rolls_back_everything(client: TestClient) -> None:
    first = _create_full_supplier(client, "SUP-ROLLBACK-1")
    second = _create_full_supplier(client, "SUP-ROLLBACK-2")
    supplier_id = first["id"]

    response = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(
            first,
            legal_name="不应落库的新名称",
            registry_no=second["registry_no"],
        ),
    )

    assert response.status_code == 409, response.text
    after = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    assert after["legal_name"] == first["legal_name"]
    assert after["registry_no"] == first["registry_no"]
    assert sorted(after["sites"], key=lambda item: item["id"]) == sorted(
        first["sites"], key=lambda item: item["id"]
    )


def test_viewer_put_forbidden_and_anonymous_401_without_leaking_detail(
    client: TestClient, auth_as: Callable[[str, str], None]
) -> None:
    detail = _create_full_supplier(client, "SUP-PERM-1")
    supplier_id = detail["id"]

    auth_as("viewer", "edit-viewer")
    forbidden = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(detail, legal_name="越权改名"),
    )
    assert forbidden.status_code == 403
    after = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    assert after["legal_name"] == detail["legal_name"]

    anonymous = TestClient(app)
    unauth = anonymous.put(
        f"/api/v1/suppliers/{supplier_id}",
        json=_put_body(detail, legal_name="匿名改名"),
    )
    assert unauth.status_code == 401
    assert "detail" in unauth.json()
    assert "无损编辑测试有限公司" not in unauth.text


# ------------------------------------------------------- ORM 级白盒与并发


def _seed_supplier_row() -> dict[str, Any]:
    code = f"SUP-EDIT-{uuid4().hex[:10]}"
    now = datetime.now(UTC)
    with SessionLocal() as session:
        supplier = Supplier(
            supplier_code=code,
            legal_name="白盒编辑供应商",
            country_code="CN",
            registry_no=None,
            registration_address="白盒登记地址",
            industry="白盒行业",
            raw_materials=["白盒材料"],
            enabled=True,
            updated_at=now,
        )
        session.add(supplier)
        session.flush()
        aliases = [
            SupplierAlias(
                alias="白盒别名甲",
                language="zh",
                normalized_alias=normalize_alias("白盒别名甲"),
            ),
            SupplierAlias(
                alias="White Box",
                language="en",
                normalized_alias=normalize_alias("White Box"),
            ),
        ]
        sites = [
            SupplierSite(
                site_name="白盒工厂甲",
                country_code="CN",
                region="浙江省",
                city="杭州市",
                district="滨江区",
                address="白盒路1号",
                latitude=None,
                longitude=None,
            ),
            SupplierSite(
                site_name="白盒工厂乙",
                country_code="CN",
                region="广东省",
                city="深圳市",
                district="南山区",
                address="白盒路2号",
                latitude=None,
                longitude=None,
            ),
        ]
        products = [SupplierProduct(name="白盒产品", keywords=["白盒关键词"])]
        supplier.aliases = aliases
        supplier.sites = sites
        supplier.products = products
        session.flush()
        session.refresh(supplier)
        row: dict[str, Any] = {
            "id": supplier.id,
            "code": code,
            "updated_at": supplier.updated_at,
            "aliases": [
                {"id": alias.id, "alias": alias.alias, "language": alias.language}
                for alias in aliases
            ],
            "sites": [
                {
                    "id": site.id,
                    "site_name": site.site_name,
                    "country_code": site.country_code,
                    "region": site.region,
                    "city": site.city,
                    "district": site.district,
                    "address": site.address,
                }
                for site in sites
            ],
            "products": [
                {"id": product.id, "name": product.name, "keywords": product.keywords}
                for product in products
            ],
        }
        session.commit()
    return row


def _make_update(row: dict[str, Any], legal_name: str) -> SupplierUpdate:
    return SupplierUpdate(
        legal_name=legal_name,
        country_code="CN",
        registry_no=None,
        registration_address="白盒登记地址",
        industry="白盒行业",
        raw_materials=["白盒材料"],
        enabled=True,
        aliases=[
            AliasInput(id=alias["id"], alias=alias["alias"], language=alias["language"])
            for alias in row["aliases"]
        ],
        sites=[
            SiteInput(
                id=site["id"],
                site_name=site["site_name"],
                country_code=site["country_code"],
                region=site["region"],
                city=site["city"],
                district=site["district"],
                address=site["address"],
            )
            for site in row["sites"]
        ],
        products=[
            ProductInput(id=product["id"], name=product["name"], keywords=product["keywords"])
            for product in row["products"]
        ],
    )


@pytest.fixture
def orm_supplier_row() -> Generator[dict[str, Any]]:
    row = _seed_supplier_row()
    try:
        yield row
    finally:
        with SessionLocal.begin() as session:
            session.execute(
                delete(Supplier).where(Supplier.supplier_code == row["code"])
            )


def test_controlled_sync_updates_existing_rows_without_rebuild(
    orm_supplier_row: dict[str, Any],
) -> None:
    supplier_id = orm_supplier_row["id"]
    with SessionLocal() as session:
        supplier = lock_supplier_for_update(session, supplier_id)
        assert_version_matches(supplier, orm_supplier_row["updated_at"])
        original_sites = {site.id: site for site in supplier.sites}
        original_aliases = {alias.id: alias for alias in supplier.aliases}
        original_products = {product.id: product for product in supplier.products}

        apply_supplier_update(
            supplier, _make_update(orm_supplier_row, "白盒编辑供应商（改）")
        )

        # 未增删的既有行必须是同一 ORM 实例：证明走 UPDATE 而非 clear+重建。
        assert {site.id for site in supplier.sites} == set(original_sites)
        for site in supplier.sites:
            assert site is original_sites[site.id]
        for alias in supplier.aliases:
            assert alias is original_aliases[alias.id]
        for product in supplier.products:
            assert product is original_products[product.id]
        session.commit()

    with SessionLocal() as session:
        row_ids = {
            "aliases": set(
                session.scalars(
                    select(SupplierAlias.id).where(
                        SupplierAlias.supplier_id == supplier_id
                    )
                )
            ),
            "sites": set(
                session.scalars(
                    select(SupplierSite.id).where(SupplierSite.supplier_id == supplier_id)
                )
            ),
            "products": set(
                session.scalars(
                    select(SupplierProduct.id).where(
                        SupplierProduct.supplier_id == supplier_id
                    )
                )
            ),
        }
    assert row_ids["aliases"] == {alias["id"] for alias in orm_supplier_row["aliases"]}
    assert row_ids["sites"] == {site["id"] for site in orm_supplier_row["sites"]}
    assert row_ids["products"] == {product["id"] for product in orm_supplier_row["products"]}


def test_second_commit_on_same_version_gets_409_supplier_changed(
    orm_supplier_row: dict[str, Any],
) -> None:
    supplier_id = orm_supplier_row["id"]
    barrier = Barrier(2)
    names = ["并发甲有限公司", "并发乙有限公司"]

    def commit_update(name: str) -> str:
        barrier.wait(timeout=10)
        with SessionLocal() as session:
            supplier = lock_supplier_for_update(session, supplier_id)
            assert_version_matches(supplier, orm_supplier_row["updated_at"])
            apply_supplier_update(supplier, _make_update(orm_supplier_row, name))
            session.commit()
        return "ok"

    def commit_update_conflict(name: str) -> str:
        try:
            return commit_update(name)
        except HTTPException as exc:
            detail = exc.detail
            code = detail["code"] if isinstance(detail, dict) else None
            return f"conflict:{code}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit_update_conflict, names))

    assert sorted(results) == ["conflict:supplier_changed", "ok"]
    with SessionLocal() as session:
        winner = session.scalar(
            select(Supplier.legal_name).where(Supplier.id == supplier_id)
        )
    assert winner in names
