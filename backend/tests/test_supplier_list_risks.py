from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.database import engine
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier


@dataclass(frozen=True, slots=True)
class AlertSpec:
    level: str
    score: int
    status: str = "current"
    expires_at: datetime | None = None


def add_supplier(db_session: Session, code: str, *, enabled: bool = True) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"{code} 供应商",
        country_code="CN",
        registry_no=None,
        enabled=enabled,
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


def add_alert(db_session: Session, supplier: Supplier, spec: AlertSpec) -> None:
    event_row = RiskEvent(
        dedup_key=f"{supplier.supplier_code}-{spec.level}-{spec.score}-{spec.status}-{id(spec)}",
        event_type="other",
        event_subtype=None,
        severity="medium",
        summary="风险列表测试事件",
        start_at=None,
        end_at=None,
        confidence=0.9,
        facts={},
    )
    db_session.add(event_row)
    db_session.flush()
    match_row = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event_row.id,
        match_type="legal_name",
        score=spec.score,
        reasons=[],
        evidence=[],
    )
    db_session.add(match_row)
    db_session.flush()
    db_session.add(
        RiskAlert(
            match_id=match_row.id,
            level=spec.level,
            score=spec.score,
            score_detail={},
            status=spec.status,
            expires_at=spec.expires_at,
        )
    )


def test_supplier_list_returns_strongest_effective_alert_consistently(
    client: TestClient, db_session: Session
) -> None:
    # Given
    future = datetime.now(UTC) + timedelta(days=1)
    expected = {
        "RISK-P1": ("P1", 1),
        "RISK-P2": ("P2", 80),
        "RISK-P3": ("P3", 30),
        "RISK-P4": ("P4", 40),
    }
    suppliers = {code: add_supplier(db_session, code) for code in expected}
    add_alert(db_session, suppliers["RISK-P1"], AlertSpec("P2", 100, expires_at=future))
    add_alert(db_session, suppliers["RISK-P1"], AlertSpec("P1", 1, expires_at=future))
    add_alert(db_session, suppliers["RISK-P2"], AlertSpec("P2", 20, expires_at=future))
    add_alert(db_session, suppliers["RISK-P2"], AlertSpec("P2", 80, expires_at=future))
    add_alert(db_session, suppliers["RISK-P3"], AlertSpec("P3", 30))
    add_alert(db_session, suppliers["RISK-P4"], AlertSpec("P4", 40))
    db_session.commit()

    # When
    response = client.get("/api/v1/suppliers")

    # Then
    assert response.status_code == 200
    actual = {
        item["supplier_code"]: (
            item["current_risk_level"],
            item["current_risk_score"],
        )
        for item in response.json()["items"]
    }
    assert actual == expected


def test_supplier_list_excludes_expired_and_past_due_current_alerts(
    client: TestClient, db_session: Session
) -> None:
    # Given
    past = datetime.now(UTC) - timedelta(seconds=1)
    expired_status = add_supplier(db_session, "EXPIRED-STATUS")
    past_due = add_supplier(db_session, "PAST-DUE")
    no_alert = add_supplier(db_session, "NO-ALERT")
    add_alert(db_session, expired_status, AlertSpec("P1", 99, status="expired"))
    add_alert(db_session, past_due, AlertSpec("P1", 98, expires_at=past))
    db_session.commit()

    # When
    with_alert = client.get(
        "/api/v1/suppliers", params={"has_current_alert": True}
    )
    without_alert = client.get(
        "/api/v1/suppliers", params={"has_current_alert": False}
    )

    # Then
    assert with_alert.json()["items"] == []
    assert without_alert.json()["total"] == 3
    assert [item["supplier_code"] for item in without_alert.json()["items"]] == [
        "EXPIRED-STATUS",
        "NO-ALERT",
        "PAST-DUE",
    ]
    assert no_alert.id is not None
    assert all(
        item["current_risk_level"] is None
        and item["current_risk_score"] is None
        for item in without_alert.json()["items"]
    )


def test_current_alert_filter_combines_with_enabled_status(
    client: TestClient, db_session: Session
) -> None:
    # Given
    enabled = add_supplier(db_session, "CURRENT-ENABLED")
    paused = add_supplier(db_session, "CURRENT-PAUSED", enabled=False)
    add_alert(db_session, enabled, AlertSpec("P3", 33))
    add_alert(db_session, paused, AlertSpec("P2", 55))
    db_session.commit()

    # When
    response = client.get(
        "/api/v1/suppliers",
        params={"enabled": True, "has_current_alert": True},
    )

    # Then
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["supplier_code"] == "CURRENT-ENABLED"


def test_supplier_list_query_count_is_bounded_and_uses_ranked_subquery(
    client: TestClient, db_session: Session
) -> None:
    # Given
    suppliers = [add_supplier(db_session, f"BOUNDED-{index:03d}") for index in range(25)]
    for index, supplier in enumerate(suppliers):
        add_alert(db_session, supplier, AlertSpec("P4", index))
    db_session.commit()
    select_statements: list[str] = []

    def capture_select(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_select)
    try:
        # When
        response = client.get(
            "/api/v1/suppliers",
            params={"limit": 20, "has_current_alert": True},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_select)

    # Then
    assert response.status_code == 200
    assert response.json()["total"] == 25
    assert len(response.json()["items"]) == 20
    assert len(select_statements) <= 8
    combined_sql = "\n".join(select_statements).lower()
    assert "row_number() over" in combined_sql
    assert "supplier_event_matches" in combined_sql
