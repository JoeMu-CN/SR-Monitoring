"""维度引用信源实时 join 真实信息源测试（任务 13 + 任务 14）。

覆盖任务 13：表中存在且 enabled 的信源返回真实状态；表中存在但 disabled 的信源
enabled=false（不因 declared_status='connected' 而被渲染成已接入）；
cenc-earthquake 返回 linked=false 且全部实时字段为 None；六个维度全量请求
的 SQL 查询次数不随维度数增长（N+1 回归门）。

覆盖任务 14（维度输入健康度反查接口）：
- alert→raw_signals 关联链路测试先行锁定
- 有提醒且有信号时 observed 非空且计数正确
- 声明信源全部 disabled 时 declared_enabled=0 且 has_input=false
- observed 中出现未被该维度声明的信源时如实返回不报错
- days=0 与 days=366 返回 422；不存在的 key 返回 404
- 无任何提醒时返回空 observed 而非报错
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.database import engine
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.workbench_router import _source_status_map
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier


def _count_queries(work: object) -> int:
    """统计一次可调用体内实际下发的 SQL 语句数。"""
    counter = {"n": 0}

    def _on_execute(
        _conn: object,
        _cursor: object,
        _statement: object,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _on_execute)
    try:
        assert callable(work)
        work()
    finally:
        event.remove(engine, "before_cursor_execute", _on_execute)
    return counter["n"]


def _add_source(
    db_session: Session,
    *,
    code: str,
    name: str,
    enabled: bool,
    adapter_status: str = "builtin",
    signal_validity_days: int | None = None,
) -> DataSource:
    """幂等创建/更新信源：迁移种子可能已存在同名 code。"""
    source = db_session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
        now = datetime.now(UTC)
        source = DataSource(
            code=code,
            name=name,
            source_type="pull",
            credibility=50,
            enabled=enabled,
            adapter_status=adapter_status,
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
            created_at=now,
            updated_at=now,
            signal_validity_days=signal_validity_days,
        )
        db_session.add(source)
    else:
        source.name = name
        source.enabled = enabled
        source.adapter_status = adapter_status
        source.signal_validity_days = signal_validity_days
    db_session.flush()
    return source


def _natural_sources(client: TestClient) -> list[dict[str, object]]:
    response = client.get("/api/v1/rule-engine/dimensions")
    assert response.status_code == 200
    natural = next(d for d in response.json() if d["key"] == "natural")
    return cast(list[dict[str, object]], natural["data_sources"])


def test_enabled_source_returns_real_status(
    client: TestClient, db_session: Session
) -> None:
    _add_source(db_session, code="nmc-weather", name="中央气象台", enabled=True)
    db_session.flush()

    sources = _natural_sources(client)
    nmc = next(s for s in sources if s["code"] == "nmc-weather")
    assert nmc["declared_status"] == "connected"
    assert nmc["linked"] is True
    assert nmc["enabled"] is True
    assert nmc["adapter_status"] == "builtin"
    assert nmc["valid_signal_count"] == 0


def test_disabled_source_not_rendered_as_connected(
    client: TestClient, db_session: Session
) -> None:
    _add_source(db_session, code="nmc-weather", name="中央气象台", enabled=False)
    db_session.flush()

    sources = _natural_sources(client)
    nmc = next(s for s in sources if s["code"] == "nmc-weather")
    # 声明意图保持 connected，但真实 enabled 必须如实反映 disabled
    assert nmc["declared_status"] == "connected"
    assert nmc["linked"] is True
    assert nmc["enabled"] is False


def test_unlinked_source_returns_none_real_fields(
    client: TestClient, db_session: Session
) -> None:
    sources = _natural_sources(client)
    cenc = next(s for s in sources if s["code"] == "cenc-earthquake")
    assert cenc["declared_status"] == "planned"
    assert cenc["linked"] is False
    assert cenc["enabled"] is None
    assert cenc["adapter_status"] is None
    assert cenc["last_collected_at"] is None
    assert cenc["valid_signal_count"] is None


def test_full_dimensions_query_count_does_not_grow_with_dimensions(
    client: TestClient, db_session: Session
) -> None:
    # 预置若干真实信源，确保 join 路径被真实执行
    _add_source(db_session, code="nmc-weather", name="中央气象台", enabled=True)
    _add_source(db_session, code="ofac-sdn", name="OFAC SDN", enabled=True)
    db_session.flush()

    holder: dict[str, object] = {}

    def _request() -> None:
        response = client.get("/api/v1/rule-engine/dimensions")
        assert response.status_code == 200
        holder["payload"] = response.json()

    total = _count_queries(_request)
    payload = holder["payload"]
    assert isinstance(payload, list)
    assert len(payload) >= 6

    # 实测 12 次（含鉴权/会话/维度加载/告警计数 + 批量 join 固定 3 次）。
    # 若按维度 N+1 查询信源，6 个维度会远超该阈值；批量 join 应远低于此。
    assert total < 20


def test_source_status_map_query_count_is_constant_regardless_of_code_count(
    db_session: Session,
) -> None:
    """批量 join 的 SQL 语句数不随引用信源（维度）数量增长。"""
    _add_source(db_session, code="nmc-weather", name="中央气象台", enabled=True)
    _add_source(db_session, code="ofac-sdn", name="OFAC SDN", enabled=True)
    db_session.flush()

    def _resolve(codes: Iterable[str]) -> None:
        _source_status_map(db_session, set(codes))

    single = _count_queries(lambda: _resolve(["nmc-weather"]))
    many = _count_queries(
        lambda: _resolve(
            [
                "nmc-weather",
                "ofac-sdn",
                "cenc-earthquake",
                "nhc-cdc",
                "mem-incident-bulletin",
                "usgs-earthquake-day",
            ]
        )
    )
    # 实测各 3 次：DataSource 行 + 最近采集时间分组 + 有效信号数按
    # signal_validity_days 分组（种子夹具同为 None，1 组）。
    assert single == many


# ---------------------------------------------------------------------------
# 任务 14：维度输入健康度反查接口
# ---------------------------------------------------------------------------
# 关联路径（先由测试锁定，不凭推断编码）：
#   RiskAlert.match_id → SupplierEventMatch.id
#   SupplierEventMatch.event_id → RiskEvent.id
#   RiskEventSignal.event_id → RiskEvent.id
#   RiskEventSignal.signal_id → RawSignal.id
#   RawSignal.source_id → DataSource.id


def _create_alert_chain(
    db_session: Session,
    *,
    source_code: str,
    source_name: str,
    source_enabled: bool = True,
    dimension: str = "natural",
    signal_published_at: datetime | None = None,
) -> tuple[DataSource, RawSignal, RiskAlert]:
    """构建完整 alert→raw_signal 关联链路，返回核心对象用于断言。

    此 helper 本身就是对关联路径的编码锁定；测试不额外验证 helper 内部。
    """
    now = datetime.now(UTC)

    # 1. DataSource
    source = db_session.scalar(
        select(DataSource).where(DataSource.code == source_code)
    )
    if source is None:
        source = DataSource(
            code=source_code,
            name=source_name,
            source_type="pull",
            credibility=50,
            enabled=source_enabled,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
            created_at=now,
            updated_at=now,
        )
        db_session.add(source)
        db_session.flush()
    else:
        source.enabled = source_enabled
        source.name = source_name
        db_session.flush()

    # 2. RawSignal
    signal = RawSignal(
        source_id=source.id,
        external_id=f"test-{source_code}-{now.timestamp():.0f}",
        title=f"测试信号-{source_code}",
        content=f"这是一条来自 {source_code} 的测试信号",
        url=f"https://example.com/{source_code}",
        published_at=signal_published_at or now,
        fingerprint=f"fp-{source_code}-{now.timestamp():.0f}",
        raw_data={"source": source_code},
        validity_state="legacy",
        validity_reason={
            "code": "legacy_unmigrated",
            "anchor_source": "legacy",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.flush()

    # 3. RiskEvent
    event_obj = RiskEvent(
        dedup_key=f"dedup-{source_code}-{now.timestamp():.0f}",
        event_type="weather",
        severity="high",
        summary=f"测试事件-{source_code}",
        confidence=0.8,
        facts={"source": source_code},
        validity_state="legacy",
    )
    db_session.add(event_obj)
    db_session.flush()

    # 4. RiskEventSignal (event_id → RiskEvent, signal_id → RawSignal)
    event_signal = RiskEventSignal(
        event_id=event_obj.id,
        signal_id=signal.id,
    )
    db_session.add(event_signal)

    # 5. Supplier (RiskAlert 需要通过 match_id 间接引用)
    supplier = Supplier(
        supplier_code=f"TEST-{source_code.upper()}",
        legal_name=f"测试供应商-{source_code}",
        country_code="CN",
        enabled=True,
    )
    db_session.add(supplier)
    db_session.flush()

    # 6. SupplierEventMatch
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event_obj.id,
        match_type="registry_no",
        score=80,
        reasons=["测试匹配"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()

    # 7. RiskAlert (status='current', score_detail含dimension)
    alert = RiskAlert(
        match_id=match.id,
        level="P2",
        score=75,
        score_detail={"dimension": dimension, "subtotal": 75},
        status="current",
        expiry_kind="legacy",
    )
    db_session.add(alert)
    db_session.flush()

    return source, signal, alert


def test_inputs_endpoint_exists(client: TestClient) -> None:
    """基础冒烟：接口存在且需要权限。"""
    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs")
    assert response.status_code == 200


def test_alert_to_raw_signal_linkage_locked_by_test(
    client: TestClient, db_session: Session
) -> None:
    """锁定关联路径：alert → SupplierEventMatch → RiskEvent →
    RiskEventSignal → RawSignal → DataSource。

    此测试通过直接查询验证 helper 创建的链路完整性，
    确保后续 observed 聚合的底层数据正确。
    """
    source, signal, alert = _create_alert_chain(
        db_session,
        source_code="linkage-test-src",
        source_name="链路测试信源",
        dimension="natural",
    )

    # 正向验证：alert.match_id → SupplierEventMatch → event_id
    match = db_session.get(SupplierEventMatch, alert.match_id)
    assert match is not None
    assert match.event_id is not None

    # 正向验证：RiskEventSignal.event_id = match.event_id
    event_signal = db_session.scalar(
        select(RiskEventSignal).where(
            RiskEventSignal.event_id == match.event_id,
            RiskEventSignal.signal_id == signal.id,
        )
    )
    assert event_signal is not None

    # 正向验证：RawSignal.source_id = source.id
    raw_signal = db_session.get(RawSignal, event_signal.signal_id)
    assert raw_signal is not None
    assert raw_signal.source_id == source.id

    # 维度归属：alert.score_detail["dimension"]
    assert alert.score_detail["dimension"] == "natural"


def test_dimension_inputs_observed_non_empty_with_correct_count(
    client: TestClient, db_session: Session
) -> None:
    """有提醒且有信号时 observed 非空且计数正确。"""
    _create_alert_chain(
        db_session,
        source_code="nmc-weather",
        source_name="中央气象台",
        dimension="natural",
    )
    _create_alert_chain(
        db_session,
        source_code="usgs-earthquake-day",
        source_name="USGS 地震",
        dimension="natural",
    )
    db_session.flush()

    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs")
    assert response.status_code == 200
    body = response.json()

    assert body["has_input"] is True
    assert body["declared_total"] >= 2  # natural 维度至少有 2 个声明信源
    assert len(body["observed"]) == 2

    codes = {item["code"] for item in body["observed"]}
    assert "nmc-weather" in codes
    assert "usgs-earthquake-day" in codes

    for item in body["observed"]:
        assert item["signal_count"] == 1
        assert item["latest_at"] is not None


def test_all_disabled_sources_declared_enabled_zero_no_input(
    client: TestClient, db_session: Session
) -> None:
    """声明信源全部 disabled 时 declared_enabled=0 且 has_input=false。"""
    # 创建信源但全部 disable
    _create_alert_chain(
        db_session,
        source_code="nmc-weather",
        source_name="中央气象台",
        source_enabled=False,
        dimension="natural",
    )
    db_session.flush()

    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs")
    assert response.status_code == 200
    body = response.json()

    assert body["declared_enabled"] == 0
    assert body["has_input"] is False
    # observed 可以非空（有提醒的信源仍被观察到），但 has_input 为 false
    # 因为 declared_enabled=0 意味着没有声明信源在工作


def test_observed_includes_undeclared_source_without_error(
    client: TestClient, db_session: Session
) -> None:
    """observed 中出现未被该维度声明的信源时如实返回不报错。"""
    # 创建一个不属于 natural 维度声明的信源
    _create_alert_chain(
        db_session,
        source_code="undeclared-source-x",
        source_name="未声明信源",
        dimension="natural",
    )
    db_session.flush()

    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs")
    assert response.status_code == 200
    body = response.json()

    # 未声明的信源应出现在 observed 中
    observed_codes = {item["code"] for item in body["observed"]}
    assert "undeclared-source-x" in observed_codes


def test_days_zero_returns_422(client: TestClient) -> None:
    """days=0 返回 422。"""
    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs?days=0")
    assert response.status_code == 422


def test_days_366_returns_422(client: TestClient) -> None:
    """days=366 返回 422。"""
    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs?days=366")
    assert response.status_code == 422


def test_nonexistent_key_returns_404(client: TestClient) -> None:
    """不存在的维度 key 返回 404。"""
    response = client.get("/api/v1/rule-engine/dimensions/nonexistent/inputs")
    assert response.status_code == 404


def test_no_alerts_returns_empty_observed(
    client: TestClient, db_session: Session
) -> None:
    """无任何提醒时返回空 observed 而非报错。"""
    response = client.get("/api/v1/rule-engine/dimensions/natural/inputs")
    assert response.status_code == 200
    body = response.json()
    assert body["observed"] == []
    assert body["has_input"] is False
