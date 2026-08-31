from sqlalchemy import func, select

from app.auth.models import User
from app.database import SessionLocal
from app.risks.models import EventEntity, EventLocation, RiskAlert, RiskEventSignal
from app.suppliers.models import Supplier


def test_e2e_seed_has_deterministic_browser_fixtures() -> None:
    # Given
    expected_codes = [f"E2E-SUP-{index:03d}" for index in range(1, 26)]

    # When
    with SessionLocal() as session:
        users = list(
            session.execute(
                select(User.username, User.role)
                .where(User.username.in_(["e2e-platform-admin", "e2e-viewer"]))
                .order_by(User.username)
            ).all()
        )
        supplier_codes = list(
            session.scalars(select(Supplier.supplier_code).order_by(Supplier.supplier_code))
        )
        alert_statuses = list(
            session.scalars(select(RiskAlert.status).order_by(RiskAlert.id))
        )
        evidence_counts = (
            session.scalar(select(func.count()).select_from(RiskEventSignal)),
            session.scalar(select(func.count()).select_from(EventEntity)),
            session.scalar(select(func.count()).select_from(EventLocation)),
        )

    # Then
    assert users == [("e2e-platform-admin", "platform_admin"), ("e2e-viewer", "viewer")]
    assert supplier_codes == expected_codes
    assert alert_statuses == ["current", "expired"]
    assert evidence_counts == (2, 2, 2)
