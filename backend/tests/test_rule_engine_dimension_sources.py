"""维度引用信源实时 join 真实数据源测试（任务 13）。

覆盖：表中存在且 enabled 的信源返回真实状态；表中存在但 disabled 的信源
enabled=false（不因 declared_status='connected' 而被渲染成已接入）；
cenc-earthquake 返回 linked=false 且全部实时字段为 None；六个维度全量请求
的 SQL 查询次数不随维度数增长（N+1 回归门）。
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.database import engine
from app.risks.workbench_router import _source_status_map
from app.signals.models import DataSource


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
