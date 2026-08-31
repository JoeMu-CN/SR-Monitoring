from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct


def test_supplier_list_searches_every_supported_field(
    client: TestClient, db_session: Session
) -> None:
    # Given
    suppliers = [
        Supplier(
            supplier_code="Needle-Code",
            legal_name="代码匹配供应商",
            country_code="CN",
            registry_no=None,
            enabled=True,
        ),
        Supplier(
            supplier_code="SEARCH-002",
            legal_name="Legal Needle Holdings",
            country_code="CN",
            registry_no=None,
            enabled=True,
        ),
        Supplier(
            supplier_code="SEARCH-003",
            legal_name="注册号匹配供应商",
            country_code="CN",
            registry_no="Registry-Needle-003",
            enabled=True,
        ),
        Supplier(
            supplier_code="SEARCH-004",
            legal_name="原始别名匹配供应商",
            country_code="CN",
            registry_no=None,
            enabled=True,
            aliases=[
                SupplierAlias(
                    alias="Alias Needle Name",
                    normalized_alias="alias needle name",
                )
            ],
        ),
        Supplier(
            supplier_code="SEARCH-005",
            legal_name="规范别名匹配供应商",
            country_code="CN",
            registry_no=None,
            enabled=True,
            aliases=[
                SupplierAlias(
                    alias="ＮＦＫＣ　ＮＡＭＥ",
                    normalized_alias="nfkc name",
                )
            ],
        ),
        Supplier(
            supplier_code="SEARCH-006",
            legal_name="产品名匹配供应商",
            country_code="CN",
            registry_no=None,
            enabled=True,
            products=[SupplierProduct(name="Product Needle", keywords=[])],
        ),
        Supplier(
            supplier_code="SEARCH-007",
            legal_name="产品关键词匹配供应商",
            country_code="CN",
            registry_no=None,
            enabled=True,
            products=[
                SupplierProduct(name="普通产品", keywords=["Keyword Needle", "其他"])
            ],
        ),
    ]
    db_session.add_all(suppliers)
    db_session.commit()
    cases = (
        ("  needle-code  ", "Needle-Code"),
        ("LEGAL NEEDLE", "SEARCH-002"),
        ("registry-needle", "SEARCH-003"),
        ("alias needle", "SEARCH-004"),
        ("NFKC NAME", "SEARCH-005"),
        ("product needle", "SEARCH-006"),
        ("keyword needle", "SEARCH-007"),
    )

    # When / Then
    for query, expected_code in cases:
        response = client.get("/api/v1/suppliers", params={"q": query})
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert [item["supplier_code"] for item in response.json()["items"]] == [
            expected_code
        ]


def test_supplier_list_filters_enabled_both_ways(
    client: TestClient, db_session: Session
) -> None:
    # Given
    db_session.add_all(
        [
            Supplier(
                supplier_code="ENABLED-001",
                legal_name="启用供应商",
                country_code="CN",
                registry_no=None,
                enabled=True,
            ),
            Supplier(
                supplier_code="PAUSED-001",
                legal_name="暂停供应商",
                country_code="CN",
                registry_no=None,
                enabled=False,
            ),
        ]
    )
    db_session.commit()

    # When
    enabled = client.get("/api/v1/suppliers", params={"enabled": True})
    paused = client.get("/api/v1/suppliers", params={"enabled": False})

    # Then
    assert [item["supplier_code"] for item in enabled.json()["items"]] == [
        "ENABLED-001"
    ]
    assert [item["supplier_code"] for item in paused.json()["items"]] == [
        "PAUSED-001"
    ]


def test_supplier_list_pages_stably_without_duplicates_or_omissions(
    client: TestClient, db_session: Session
) -> None:
    # Given
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Supplier(
                supplier_code=f"PAGE-{index:03d}",
                legal_name=f"分页供应商 {index:03d}",
                country_code="CN",
                registry_no=None,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            for index in range(27, 0, -1)
        ]
    )
    db_session.commit()

    # When
    first = client.get("/api/v1/suppliers", params={"limit": 20, "offset": 0})
    second = client.get("/api/v1/suppliers", params={"limit": 20, "offset": 20})
    outside = client.get("/api/v1/suppliers", params={"limit": 20, "offset": 40})

    # Then
    first_codes = [item["supplier_code"] for item in first.json()["items"]]
    second_codes = [item["supplier_code"] for item in second.json()["items"]]
    assert first.json()["total"] == second.json()["total"] == 27
    assert first_codes + second_codes == [f"PAGE-{index:03d}" for index in range(1, 28)]
    assert len(set(first_codes + second_codes)) == 27
    assert all(
        item["current_risk_level"] is None
        and item["current_risk_score"] is None
        for item in first.json()["items"]
    )
    assert outside.json() == {"items": [], "total": 27, "limit": 20, "offset": 40}


def test_supplier_list_rejects_invalid_query_values(client: TestClient) -> None:
    # Given / When
    responses = (
        client.get("/api/v1/suppliers", params={"limit": 0}),
        client.get("/api/v1/suppliers", params={"limit": 101}),
        client.get("/api/v1/suppliers", params={"offset": -1}),
        client.get("/api/v1/suppliers", params={"enabled": "paused"}),
        client.get("/api/v1/suppliers", params={"has_current_alert": "unknown"}),
        client.get("/api/v1/suppliers", params={"q": "x" * 101}),
    )

    # Then
    assert all(response.status_code == 422 for response in responses)
