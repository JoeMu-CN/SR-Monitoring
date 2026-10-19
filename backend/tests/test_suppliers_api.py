from collections.abc import Callable
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.suppliers.importer import (
    SHEET_PRODUCTS,
    SHEET_SITES,
    SHEET_SUPPLIERS,
    create_template,
)
from app.suppliers.models import Supplier

SETTLEMENT_CURRENCY_ERROR = "结算币种必须是三个大写英文字母"


def upload(client: TestClient, content: bytes) -> Response:
    return client.post(
        "/api/v1/suppliers/import",
        files={
            "file": (
                "suppliers.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def currency_workbook(currency: object, supplier_code: str) -> bytes:
    """按最新模板位置写入「结算币种」列（可选列，位于「启用监控」之后）。"""
    workbook = load_workbook(BytesIO(create_template()))
    workbook[SHEET_SUPPLIERS].append(
        [
            supplier_code,
            "结算币种供应商",
            "CN",
            None,
            None,
            None,
            None,
            None,
            True,
            currency,
        ]
    )
    workbook[SHEET_SITES].append(
        [
            supplier_code,
            "币种工厂",
            "CN",
            "上海市",
            "上海市",
            "浦东新区",
            "上海市浦东新区币种路1号",
            None,
            None,
        ]
    )
    workbook[SHEET_PRODUCTS].append([supplier_code, "测试产品", None])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def currency_payload(code: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "supplier_code": code,
        "legal_name": "结算币种测试供应商",
        "country_code": "CN",
        "enabled": True,
        "aliases": [],
        "sites": [],
        "products": [],
    }
    payload.update(overrides)
    return payload


def test_template_download(client: TestClient) -> None:
    response = client.get("/api/v1/suppliers/import-template")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert load_workbook(BytesIO(response.content)).sheetnames == [
        "供应商",
        "生产地点",
        "供应产品",
    ]


def test_import_is_idempotent_and_searchable(
    client: TestClient, workbook_factory: Callable[..., bytes]
) -> None:
    first = upload(client, workbook_factory())
    second = upload(client, workbook_factory(legal_name="测试供应商股份有限公司"))

    assert first.status_code == 200
    assert first.json()["created_suppliers"] == 1
    assert second.status_code == 200
    assert second.json()["updated_suppliers"] == 1

    response = client.get("/api/v1/suppliers", params={"q": "Test Supplier", "enabled": True})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["legal_name"] == "测试供应商股份有限公司"
    assert len(body["items"][0]["sites"]) == 1
    assert len(body["items"][0]["products"]) == 1


def test_invalid_import_is_atomic(
    client: TestClient, workbook_factory: Callable[..., bytes]
) -> None:
    assert upload(client, workbook_factory()).status_code == 200
    workbook = load_workbook(BytesIO(workbook_factory(supplier_code="SUP-0002")))
    workbook[SHEET_SITES]["G2"] = 100
    output = BytesIO()
    workbook.save(output)

    invalid = upload(client, output.getvalue())

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["errors"][0]["sheet"] == SHEET_SITES
    assert client.get("/api/v1/suppliers").json()["total"] == 1


def test_create_update_and_disable_supplier(client: TestClient) -> None:
    create_payload = {
        "supplier_code": "SUP-API-1",
        "legal_name": "API供应商有限公司",
        "country_code": "cn",
        "registry_no": None,
        "registration_address": "江苏省苏州市姑苏区登记路1号",
        "enabled": True,
        "aliases": [{"alias": "API Supplier", "language": "en"}],
        "sites": [
            {
                "site_name": "苏州工厂",
                "country_code": "CN",
                "region": "江苏省",
                "city": "苏州市",
                "district": "姑苏区",
                "address": "苏州市测试路1号",
                "latitude": 31.2989,
                "longitude": 120.5853,
            }
        ],
        "products": [{"name": "连接器", "keywords": ["电子元件"]}],
    }
    created = client.post("/api/v1/suppliers", json=create_payload)
    assert created.status_code == 201
    supplier_id = created.json()["id"]

    update_payload = {key: value for key, value in create_payload.items() if key != "supplier_code"}
    update_payload["legal_name"] = "API供应商股份有限公司"
    updated = client.put(f"/api/v1/suppliers/{supplier_id}", json=update_payload)
    assert updated.status_code == 200
    assert updated.json()["legal_name"] == "API供应商股份有限公司"
    assert updated.json()["registration_address"] == "江苏省苏州市姑苏区登记路1号"
    assert updated.json()["sites"][0]["district"] == "姑苏区"

    disabled = client.patch(
        f"/api/v1/suppliers/{supplier_id}/enabled",
        json={"enabled": False},
        headers={"X-User-Role": "admin"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert client.get("/api/v1/suppliers", params={"enabled": False}).json()["total"] == 1


def test_supplier_keeps_registration_separate_from_multiple_sites(client: TestClient) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json={
            "supplier_code": "SUP-MULTI-SITE",
            "legal_name": "多生产地址供应商",
            "country_code": "CN",
            "registry_no": None,
            "registration_address": "广东省深圳市福田区登记路1号",
            "sites": [
                {
                    "site_name": "深圳工厂",
                    "country_code": "CN",
                    "region": "广东省",
                    "city": "深圳市",
                    "district": "南山区",
                    "address": "广东省深圳市南山区生产路1号",
                },
                {
                    "site_name": "东莞工厂",
                    "country_code": "CN",
                    "region": "广东省",
                    "city": "东莞市",
                    "district": "松山湖",
                    "address": "广东省东莞市松山湖生产路2号",
                },
            ],
            "products": [{"name": "连接器", "keywords": []}],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["registration_address"] == "广东省深圳市福田区登记路1号"
    assert {site["address"] for site in body["sites"]} == {
        "广东省深圳市南山区生产路1号",
        "广东省东莞市松山湖生产路2号",
    }
    assert {site["district"] for site in body["sites"]} == {"南山区", "松山湖"}


def test_supplier_monitoring_toggle_requires_admin(client: TestClient, auth_as) -> None:
    payload = {
        "supplier_code": "SUP-ROLE-1",
        "legal_name": "权限测试供应商",
        "country_code": "CN",
        "registry_no": None,
        "enabled": True,
        "aliases": [],
        "sites": [],
        "products": [],
    }
    created = client.post("/api/v1/suppliers", json=payload)
    supplier_id = created.json()["id"]

    auth_as("viewer", "supplier-viewer")
    viewer = client.patch(
        f"/api/v1/suppliers/{supplier_id}/enabled",
        json={"enabled": False},
        headers={"X-User-Role": "admin"},
    )

    assert viewer.status_code == 403
    assert client.get(f"/api/v1/suppliers/{supplier_id}").json()["enabled"] is True

    auth_as("risk_admin", "supplier-risk-admin")
    admin = client.patch(
        f"/api/v1/suppliers/{supplier_id}/enabled",
        json={"enabled": False},
    )
    assert admin.status_code == 200
    assert admin.json()["enabled"] is False


def test_delete_supplier_requires_admin_and_cascades(
    client: TestClient, auth_as: Callable[[str, str], None]
) -> None:
    payload = {
        "supplier_code": "SUP-DEL-1",
        "legal_name": "删除测试供应商",
        "country_code": "CN",
        "registry_no": None,
        "enabled": True,
        "aliases": [{"alias": "delalias", "language": None}],
        "sites": [{
            "site_name": "工厂A", "country_code": "CN",
            "region": "广东省", "city": "深圳市", "district": "南山区",
            "address": "科技园1号", "latitude": None, "longitude": None,
        }],
        "products": [{"name": "测试产品", "keywords": []}],
    }
    created = client.post("/api/v1/suppliers", json=payload)
    assert created.status_code == 201
    supplier_id = created.json()["id"]

    # viewer 无权删除
    auth_as("viewer", "del-viewer")
    forbidden = client.delete(f"/api/v1/suppliers/{supplier_id}")
    assert forbidden.status_code == 403
    assert client.get(f"/api/v1/suppliers/{supplier_id}").status_code == 200

    # admin 可删除，返回 204，资源消失
    auth_as("risk_admin", "del-admin")
    ok = client.delete(f"/api/v1/suppliers/{supplier_id}")
    assert ok.status_code == 204
    assert client.get(f"/api/v1/suppliers/{supplier_id}").status_code == 404

    # 重复删除返回 404
    again = client.delete(f"/api/v1/suppliers/{supplier_id}")
    assert again.status_code == 404


# ---------------------------------------------------- 结算币种（ISO 4217）


@pytest.mark.parametrize("invalid", ["US", "USDT", "US1", "人民币", "USD-"])
def test_settlement_currency_rejects_non_iso_values(
    client: TestClient, invalid: str
) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json=currency_payload("SUP-CUR-BAD", settlement_currency=invalid),
    )

    assert response.status_code == 422, response.text
    assert SETTLEMENT_CURRENCY_ERROR in response.text
    assert client.get("/api/v1/suppliers").json()["total"] == 0


def test_settlement_currency_lowercase_is_normalized_to_upper(client: TestClient) -> None:
    response = client.post(
        "/api/v1/suppliers",
        json=currency_payload("SUP-CUR-LOWER", settlement_currency="  usd  "),
    )

    assert response.status_code == 201, response.text
    assert response.json()["settlement_currency"] == "USD"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_settlement_currency_is_optional_and_blank_becomes_none(
    client: TestClient, blank: str | None
) -> None:
    payload = currency_payload("SUP-CUR-BLANK")
    if blank is not None:
        payload["settlement_currency"] = blank
    else:
        payload.pop("settlement_currency", None)

    created = client.post("/api/v1/suppliers", json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["settlement_currency"] is None


def test_settlement_currency_is_kept_and_updatable(client: TestClient) -> None:
    created = client.post(
        "/api/v1/suppliers",
        json=currency_payload("SUP-CUR-UPDATE", settlement_currency="CNY"),
    )
    assert created.status_code == 201, created.text
    supplier_id = created.json()["id"]

    unchanged = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json={
            key: value
            for key, value in created.json().items()
            if key not in {"id", "supplier_code"}
        }
        | {"legal_name": "结算币种测试供应商（改）"},
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["settlement_currency"] == "CNY"

    changed = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json={
            key: value
            for key, value in unchanged.json().items()
            if key not in {"id", "supplier_code"}
        }
        | {"settlement_currency": "jpy"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["settlement_currency"] == "JPY"

    cleared = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json={
            key: value
            for key, value in changed.json().items()
            if key not in {"id", "supplier_code"}
        }
        | {"settlement_currency": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["settlement_currency"] is None

    invalid = client.put(
        f"/api/v1/suppliers/{supplier_id}",
        json={
            key: value
            for key, value in cleared.json().items()
            if key not in {"id", "supplier_code"}
        }
        | {"settlement_currency": "USDT"},
    )
    assert invalid.status_code == 422, invalid.text
    assert SETTLEMENT_CURRENCY_ERROR in invalid.text
    assert client.get(f"/api/v1/suppliers/{supplier_id}").json()["settlement_currency"] is None


def test_import_settlement_currency_is_persisted_as_uppercase(
    client: TestClient, db_session: Session
) -> None:
    created = upload(client, currency_workbook("jpy", "SUP-CUR-IMPORT"))

    assert created.status_code == 200, created.text
    assert created.json()["created_suppliers"] == 1
    stored = db_session.scalar(
        select(Supplier.settlement_currency).where(
            Supplier.supplier_code == "SUP-CUR-IMPORT"
        )
    )
    assert stored == "JPY"
    assert (
        client.get("/api/v1/suppliers", params={"q": "结算币种供应商"}).json()["items"][0][
            "settlement_currency"
        ]
        == "JPY"
    )


def test_import_invalid_settlement_currency_is_rejected_atomically(
    client: TestClient,
) -> None:
    response = upload(client, currency_workbook("USDT", "SUP-CUR-IMPORT-BAD"))

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]["errors"]
    assert any(
        error["field"] == "settlement_currency" and SETTLEMENT_CURRENCY_ERROR in error["message"]
        for error in errors
    ), errors
    assert client.get("/api/v1/suppliers").json()["total"] == 0
