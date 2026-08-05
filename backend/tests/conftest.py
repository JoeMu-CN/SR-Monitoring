from collections.abc import Callable, Generator
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.database import engine, get_session
from app.main import app
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
    session.execute(delete(AIAnalysisRecord))
    session.execute(delete(RawSignal))
    session.execute(delete(CollectionRun))
    session.execute(delete(Supplier))
    session.flush()
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient]:
    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def workbook_factory() -> Callable[..., bytes]:
    def build(
        *,
        supplier_code: str = "SUP-0001",
        legal_name: str = "测试供应商有限公司",
        enabled: bool = True,
        latitude: object = 31.2304,
        longitude: object = 121.4737,
    ) -> bytes:
        workbook = load_workbook(BytesIO(create_template()))
        workbook[SHEET_SUPPLIERS].append(
            [
                supplier_code,
                legal_name,
                "CN",
                "91310000TEST00001",
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
