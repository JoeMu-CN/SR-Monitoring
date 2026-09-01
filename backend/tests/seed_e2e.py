import json
from datetime import UTC, datetime
from typing import Final, TypedDict

from sqlalchemy import select
from e2e_source_signal_fixtures import add_source_signal_fixtures
from test_stack_guard import require_test_database_url

from app.auth.models import User
from app.auth.security import hash_password
from app.database import SessionLocal, engine
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier, SupplierAlias, SupplierProduct, SupplierSite

ADMIN_USERNAME: Final = "e2e-platform-admin"
VIEWER_USERNAME: Final = "e2e-viewer"
TEST_PASSWORD: Final = "E2E-Test-Only-2026!"
FIXED_NOW: Final = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)


class SeedReceipt(TypedDict):
    database: str
    users: list[str]
    supplier_count: int
    supplier_codes: list[str]
    alert_statuses: list[str]
    evidence_records: int


def seed() -> SeedReceipt:
    require_test_database_url(str(engine.url))
    supplier_codes = [f"E2E-SUP-{index:03d}" for index in range(1, 26)]
    with SessionLocal.begin() as session:
        if session.scalar(select(User.id).where(User.username == ADMIN_USERNAME)) is not None:
            return _receipt(supplier_codes)

        session.add_all(
            [
                User(
                    id=90_001,
                    username=ADMIN_USERNAME,
                    password_hash=hash_password(TEST_PASSWORD),
                    display_name="E2E Platform Admin",
                    role="platform_admin",
                    status="active",
                    password_changed_at=FIXED_NOW,
                ),
                User(
                    id=90_002,
                    username=VIEWER_USERNAME,
                    password_hash=hash_password(TEST_PASSWORD),
                    display_name="E2E Viewer",
                    role="viewer",
                    status="active",
                    password_changed_at=FIXED_NOW,
                ),
            ]
        )
        source = DataSource(
            id=91_000,
            code="e2e-public-source",
            name="E2E Public Source",
            source_type="official",
            credibility=95,
            endpoint_url="https://example.test/risk-feed",
            auth_type="none",
            login_config={},
            adapter_config={},
            adapter_status="builtin",
            adapter_version=1,
            enabled=True,
            signal_validity_days=10,
            created_at=FIXED_NOW,
            updated_at=FIXED_NOW,
        )
        session.add(source)
        suppliers: list[Supplier] = []
        for index, supplier_code in enumerate(supplier_codes, start=1):
            supplier = Supplier(
                id=92_000 + index,
                supplier_code=supplier_code,
                legal_name=f"E2E Supplier {index:03d}",
                country_code="CN",
                registry_no=f"E2E-REG-{index:03d}",
                registration_address=f"E2E Registration Address {index:03d}",
                industry="precision-components",
                raw_materials=["steel", "copper"],
                enabled=index != 25,
                created_at=FIXED_NOW,
                updated_at=FIXED_NOW,
            )
            supplier.aliases.append(
                SupplierAlias(
                    alias=f"E2E Alias {index:03d}",
                    language="en",
                    normalized_alias=f"e2e alias {index:03d}",
                )
            )
            supplier.sites.append(
                SupplierSite(
                    site_name=f"E2E Site {index:03d}",
                    country_code="CN",
                    region="Shanghai",
                    city="Shanghai",
                    district="Pudong",
                    address=f"E2E Production Road {index:03d}",
                    latitude=None,
                    longitude=None,
                )
            )
            supplier.products.append(
                SupplierProduct(
                    name=f"E2E Component {index:03d}",
                    keywords=["e2e", f"component-{index:03d}"],
                )
            )
            suppliers.append(supplier)
        session.add_all(suppliers)
        session.flush()

        for offset, status in enumerate(("current", "expired"), start=1):
            event_id = 93_000 + offset
            signal_id = 94_000 + offset
            match_id = 95_000 + offset
            session.add(
                RawSignal(
                    id=signal_id,
                    source_id=source.id,
                    external_id=f"E2E-SIGNAL-{offset}",
                    title=f"E2E Evidence Signal {offset}",
                    content=f"Verified evidence for {supplier_codes[offset - 1]}",
                    url=f"https://example.test/evidence/{offset}",
                    published_at=FIXED_NOW,
                    collected_at=FIXED_NOW,
                    fingerprint=f"e2e-fingerprint-{offset}",
                    raw_data={"fixture": True, "sequence": offset},
                )
            )
            session.add(
                RiskEvent(
                    id=event_id,
                    dedup_key=f"e2e-event-{offset}",
                    event_type="supplier_entity",
                    event_subtype="operations_disruption",
                    severity="high" if status == "current" else "medium",
                    summary=f"E2E risk event {offset}",
                    start_at=FIXED_NOW,
                    end_at=None,
                    confidence=0.95,
                    facts={"fixture": True, "sequence": offset},
                    created_at=FIXED_NOW,
                    updated_at=FIXED_NOW,
                )
            )
            session.flush()
            session.add_all(
                [
                    RiskEventSignal(event_id=event_id, signal_id=signal_id),
                    EventEntity(
                        id=96_000 + offset,
                        event_id=event_id,
                        name=suppliers[offset - 1].legal_name,
                        normalized_name=suppliers[offset - 1].legal_name.lower(),
                        registry_no=suppliers[offset - 1].registry_no,
                    ),
                    EventLocation(
                        id=97_000 + offset,
                        event_id=event_id,
                        name="E2E Shanghai Site",
                        normalized_name="e2e shanghai site",
                        country_code="CN",
                        region="Shanghai",
                        city="Shanghai",
                        district="Pudong",
                        latitude=31.2304,
                        longitude=121.4737,
                        radius_km=10.0,
                    ),
                    SupplierEventMatch(
                        id=match_id,
                        supplier_id=suppliers[offset - 1].id,
                        event_id=event_id,
                        match_type="registry_no+site",
                        score=90 if status == "current" else 70,
                        reasons=["registry_no_exact", "production_site_overlap"],
                        evidence=[
                            {
                                "signal_id": signal_id,
                                "source_code": source.code,
                                "evidence_type": "official_record",
                            }
                        ],
                        created_at=FIXED_NOW,
                    ),
                ]
            )
            session.flush()
            session.add(
                RiskAlert(
                    id=98_000 + offset,
                    match_id=match_id,
                    level="P1" if status == "current" else "P3",
                    score=90 if status == "current" else 70,
                    score_detail={"rule_version": "e2e-v1", "evidence": 95},
                    status=status,
                    expires_at=None if status == "current" else FIXED_NOW,
                    created_at=FIXED_NOW,
                    updated_at=FIXED_NOW,
                )
            )

        add_source_signal_fixtures(session, source.id, FIXED_NOW)
    return _receipt(supplier_codes)


def _receipt(supplier_codes: list[str]) -> SeedReceipt:
    return {
        "database": "supplier_risk_test",
        "users": [ADMIN_USERNAME, VIEWER_USERNAME],
        "supplier_count": len(supplier_codes),
        "supplier_codes": supplier_codes,
        "alert_statuses": ["current", "expired"],
        "evidence_records": 2,
    }


if __name__ == "__main__":
    print(json.dumps(seed(), sort_keys=True))
