from collections.abc import Callable, Generator
from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from test_stack_guard import UnsafeTestDatabaseError, require_test_database_url

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
from app.scheduler.runtime_models import SchedulerRuntimeState
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.suppliers.importer import (
    SHEET_PRODUCTS,
    SHEET_SITES,
    SHEET_SUPPLIERS,
    create_template,
)
from app.suppliers.models import Supplier


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    try:
        require_test_database_url(str(engine.url))
    except UnsafeTestDatabaseError as error:
        raise pytest.UsageError(str(error)) from None


@pytest.fixture(autouse=True)
def _reset_scheduler_runtime_state() -> Generator[None]:
    """观测表由 Scheduler 用独立短事务写入，测试间必须在事务外提交式清空，
    否则未提交的 db_session 事务删除已提交行会与观测写入的 upsert 相互死锁。"""
    with engine.begin() as connection:
        connection.execute(delete(SchedulerRuntimeState))
    yield


@pytest.fixture
def committed_tyc_env() -> Generator[None]:
    """天眼查提交态隔离：快照配置、清空计费表、用例后恢复（D9 执行器跨连接可见）。

    Wave 1 起额度执行器使用独立 Session，用例对天眼查的额度/密钥/维度配置
    必须真提交才可见；计费行也会跨连接残留，因此在用例前后一律清空，
    消除跨用例状态泄漏（不弱化任何断言）。
    """
    from tyc_batch_support import (
        restore_tyc_source,
        snapshot_tyc_source,
        truncate_committed_tyc_usage,
    )

    original = snapshot_tyc_source()
    truncate_committed_tyc_usage()
    yield
    restore_tyc_source(original)
    truncate_committed_tyc_usage()


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

    # 测试不依赖 prod 种子：缺失时 seed 必要的 DataSource（事务回滚，prod 库不被污染）。
    # 当前仅 signals/import 端点需要 manual-json。生产已下线此信道，测试保留。
    manual_source = session.scalar(
        select(DataSource).where(DataSource.code == "manual-json")
    )
    test_validity_policy = {
        "mode": "fixed_days",
        "fixed_days": 3650,
        "review_required": False,
    }
    if manual_source is None:
        now = datetime.now(UTC)
        session.add(
            DataSource(
                id=1_000_000,
                code="manual-json",
                name="标准 JSON 手工导入",
                source_type="manual",
                credibility=50,
                enabled=True,
                adapter_status="builtin",
                adapter_version=0,
                auth_type="none",
                login_config={},
                adapter_config={},
                validity_policy=test_validity_policy,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        manual_source.validity_policy = test_validity_policy
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
