from collections.abc import Callable, Generator
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.auth.models import User
from app.auth.security import create_session, csrf_token_for_session
from app.config import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.database import engine, get_session
from app.main import app
from app.notification.models import NotificationDelivery, NotificationSubscription
from app.research.models import (
    ResearchBatch,
    ResearchCitation,
    ResearchClaim,
    ResearchClaimCitation,
    ResearchProviderQuotaPeriod,
    ResearchReport,
    ResearchScheduleConfig,
    ResearchSource,
    ResearchTask,
    ResearchTaskEvent,
    ResearchWorkerHeartbeat,
)
from app.risks.models import (
    EventEntity,
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    RuleDimensionConfig,
    SupplierEventMatch,
)
from app.signals.models import CollectionRun, RawSignal
from app.suppliers.importer import (
    SHEET_PRODUCTS,
    SHEET_SITES,
    SHEET_SUPPLIERS,
    create_template,
)
from app.suppliers.models import Supplier


@pytest.fixture
def db_session() -> Generator[Session]:
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    session.execute(delete(NotificationDelivery))
    session.execute(delete(NotificationSubscription))
    session.execute(delete(RiskAlert))
    session.execute(delete(SupplierEventMatch))
    session.execute(delete(RiskEventSignal))
    session.execute(delete(EventLocation))
    session.execute(delete(EventEntity))
    session.execute(delete(RiskEvent))
    session.execute(delete(AIAnalysisRecord))
    session.execute(delete(RawSignal))
    session.execute(delete(CollectionRun))
    session.execute(delete(ResearchTaskEvent))
    session.execute(delete(ResearchWorkerHeartbeat))
    session.execute(delete(ResearchScheduleConfig))
    session.execute(delete(ResearchProviderQuotaPeriod))
    session.execute(delete(ResearchBatch))
    session.execute(delete(ResearchReport))
    session.execute(delete(ResearchClaimCitation))
    session.execute(delete(ResearchCitation))
    session.execute(delete(ResearchSource))
    session.execute(delete(ResearchClaim))
    session.execute(delete(ResearchTask))
    session.execute(delete(Supplier))
    session.execute(delete(RuleDimensionConfig))
    session.flush()
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


def authenticate_client(
    test_client: TestClient,
    db_session: Session,
    *,
    role: str = "platform_admin",
    username: str = "test-platform-admin",
) -> User:
    user = User(
        username=username,
        password_hash="not-used-by-test-session",
        display_name=username,
        role=role,
        status="active",
    )
    db_session.add(user)
    db_session.commit()
    token = create_session(db_session, user=user)
    csrf_token = csrf_token_for_session(token)
    test_client.cookies.set(SESSION_COOKIE_NAME, token)
    test_client.cookies.set(CSRF_COOKIE_NAME, csrf_token)
    test_client.headers["Origin"] = "http://testserver"
    test_client.headers["X-CSRF-Token"] = csrf_token
    return user


@pytest.fixture
def client(
    db_session: Session, request: pytest.FixtureRequest
) -> Generator[TestClient]:
    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        if request.path.name != "test_auth.py":
            authenticate_client(test_client, db_session)
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_as(
    client: TestClient, db_session: Session
) -> Callable[[str, str], User]:
    def authenticate(role: str, username: str) -> User:
        client.cookies.clear()
        client.headers.pop("X-CSRF-Token", None)
        return authenticate_client(
            client, db_session, role=role, username=username
        )

    return authenticate


@pytest.fixture
def workbook_factory() -> Callable[..., bytes]:
    def build(
        *,
        supplier_code: str = "SUP-0001",
        legal_name: str = "测试供应商有限公司",
        enabled: bool = True,
        latitude: object = 31.2304,
        longitude: object = 121.4737,
        industry: str | None = None,
        raw_materials: str | None = None,
    ) -> bytes:
        workbook = load_workbook(BytesIO(create_template()))
        workbook[SHEET_SUPPLIERS].append(
            [
                supplier_code,
                legal_name,
                "CN",
                "91310000TEST00001",
                "上海市浦东新区测试登记路1号",
                industry,
                raw_materials,
                "测试供应商;Test Supplier",
                enabled,
            ]
        )
        workbook[SHEET_SITES].append(
            [
                supplier_code,
                "上海工厂",
                "CN",
                "上海市",
                "上海市",
                "浦东新区",
                "上海市浦东新区测试路1号",
                latitude,
                longitude,
            ]
        )
        workbook[SHEET_PRODUCTS].append(
            [supplier_code, "精密零部件", "零部件;精密加工"]
        )
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    return build
