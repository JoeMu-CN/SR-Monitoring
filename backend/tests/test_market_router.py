"""行情指标只读视图与运行配置 API（Wave 2 / Todo 12）。

锁定的行为契约（每条都对应一种静默失效）：
1. **未登录即 401、viewer 写即 403、写接口必须有 CSRF**：行情视图继承
   ``risk_view``，配置读沿用信源状态权限，写必须 ``rule_manage``；否则浏览器端
   角色就能改线上阈值。
2. **三态语义必须来自 ``evaluate_series``** 而不是重新实现一遍阈值比较：
   触发 → ``tripped``；未触发且观察点数未达 ``min_observations`` →
   ``accumulating``；否则 ``normal``。自己算一遍会在阈值改成"含等于"时立刻漂移。
3. **停用的指标不进入视图**：``config_store`` 把 ``enabled=false`` 视同未登记，
   视图与该口径保持一致，避免"界面上还能看到、评估时已不生效"。
4. **``latest_alert_id`` 只认 ``status=current`` 且读数属于该指标**：跨指标
   误挂会让运营跳到别人的提醒上；把 expired 当最新则等于给一条已失效的提醒
   提供"继续处置"的入口。
5. **配置写入必须真的失效缓存**（``invalidate_market_config_cache``）：否则 PUT
   立刻返回、随后 GET 仍读到 TTL 内的旧值，运营会以为配置没保存成功而反复重试。
6. **DELETE 后必须回退代码默认**：删除配置行即刻读回默认，且缓存同步失效。
7. **响应只含契约字段**：不得泄露 ``raw_data`` / ``source_extra`` / 指纹 / 正文。
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import app
from app.market.config_store import (
    MARKET_CONFIG_KEY,
    invalidate_market_config_cache,
)
from app.market.registry import (
    FUTURES_SOURCE_CODE,
    INDICATORS,
    INDICATORS_BY_CODE,
    SEMICONDUCTOR_SPEC,
)
from app.market.schemas import IndicatorThresholdsRead
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    RuleDimensionConfig,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

BASE: Final = "/api/v1/market"
INDICATORS_URL: Final = f"{BASE}/indicators"
CONFIG_URL: Final = f"{BASE}/config"
CU_SYMBOL: Final = "nf_CU0"
CU_CODE: Final = "fut-CU"

#: 契约字段全集：少字段是回归，多字段（尤其原始报文）同样是回归。
ITEM_KEYS: Final = {
    "code",
    "label",
    "source_code",
    "source_name",
    "unit",
    "latest",
    "stats",
    "thresholds",
    "status",
    "series",
    "latest_alert_id",
}
LATEST_KEYS: Final = {"observed_at", "value", "change_pct"}
STATS_KEYS: Final = {"observations", "sustained_change_pct", "shock_max_day_pct"}
SERIES_KEYS: Final = {"observed_at", "value"}
CONFIG_INDICATOR_KEYS: Final = {
    "code",
    "label",
    "source_code",
    "unit",
    "enabled",
    "overridden",
    "effective",
    "defaults",
}


@pytest.fixture(autouse=True)
def _isolated_market_config_cache() -> Generator[None]:
    """配置缓存是模块级全局，用例前后强制失效，避免跨用例脏状态。"""
    invalidate_market_config_cache()
    yield
    invalidate_market_config_cache()


@pytest.fixture
def anon_client(db_session: Session) -> Generator[TestClient]:
    """未登录客户端：验证 401，而不是复用 conftest 里自动登录的 client。"""

    def override_session() -> Generator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _source(session: Session, code: str, name: str) -> DataSource:
    """复用迁移已播种的信源，缺失时新建；名称强制为测试值以便断言 join。"""
    source = session.scalar(select(DataSource).where(DataSource.code == code))
    if source is None:
        source = DataSource(
            code=code,
            name=name,
            source_type="api",
            credibility=90,
            enabled=True,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
        )
        session.add(source)
    source.name = name
    session.flush()
    return source


def _observation(
    session: Session,
    source: DataSource,
    *,
    index: int,
    value: float,
    change_pct: float | None = 0.1,
    days_ago: int | None = None,
) -> RawSignal:
    """一条沪铜观察点（``days_ago=None`` 时按 index 递增排列，全部落在过去）。"""
    collected = datetime.now(UTC) - timedelta(
        days=35 - index if days_ago is None else days_ago
    )
    extra: dict[str, object] = {"symbol": CU_SYMBOL, "last_price": value}
    if change_pct is not None:
        extra["change_pct"] = change_pct
    signal = RawSignal(
        source_id=source.id,
        external_id=f"market-api-obs-{index}",
        dedup_key=f"market-api-obs-{index}",
        title=f"沪铜主力 日行情 {index}",
        content=f"沪铜主力 nf_CU0 收盘 {value}",
        published_at=collected,
        collected_at=collected,
        fingerprint=f"market-api-obs-fp-{index}",
        raw_data={"title": f"沪铜主力 日行情 {index}", "source_extra": extra},
    )
    session.add(signal)
    session.flush()
    return signal


def _series(session: Session, values: list[float]) -> list[RawSignal]:
    source = _source(session, FUTURES_SOURCE_CODE, "期货行情（测试）")
    return [
        _observation(session, source, index=index, value=value)
        for index, value in enumerate(values)
    ]


def _add_supplier(session: Session, code: str) -> Supplier:
    supplier = Supplier(
        supplier_code=code,
        legal_name=f"{code} 有限公司",
        country_code="CN",
        registry_no=f"REG-{code}",
    )
    session.add(supplier)
    session.flush()
    return supplier


def _alert_for_signal(
    session: Session, signal: RawSignal, *, status: str = "current", suffix: str = "a"
) -> RiskAlert:
    """挂一条"信号 → 事件 → 匹配 → 提醒"链，提醒状态由 ``status`` 决定。"""
    event = RiskEvent(
        dedup_key=f"market-api-event-{suffix}-{signal.id}",
        event_type="market",
        event_subtype="commodity_price_move",
        severity="high",
        summary="沪铜价格异动（测试）",
        start_at=signal.collected_at,
        confidence=0.99,
        facts={"summary_zh": "沪铜价格异动（测试）", "evidence_sentences": []},
        validity_state="active",
        validity_reason={
            "code": "effective_signal_support",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    session.add(event)
    session.flush()
    session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    supplier = _add_supplier(session, f"SUP-API-{suffix}")
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="industry",
        score=40,
        reasons=["行业相关"],
        evidence=[],
    )
    session.add(match)
    session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level="P3",
        score=60,
        score_detail={},
        status=status,
        expires_at=None,
        expiry_kind="legacy",
    )
    session.add(alert)
    session.flush()
    return alert


def _config_row(session: Session) -> RuleDimensionConfig | None:
    session.expire_all()
    return session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
    )


def _item(body: dict[str, object], code: str = CU_CODE) -> dict[str, object]:
    items = body["items"]
    assert isinstance(items, list)
    for entry in items:  # type: ignore[union-attr]
        if entry["code"] == code:
            return entry  # type: ignore[no-any-return]
    raise AssertionError(f"响应缺少指标 {code}")


def _get_indicators(client: TestClient, expect: int = 200) -> dict[str, object]:
    response = client.get(INDICATORS_URL)
    assert response.status_code == expect, response.text
    return response.json()  # type: ignore[no-any-return]


def _put_config(
    client: TestClient, indicators: dict[str, object], *, expect: int = 200
) -> dict[str, object]:
    response = client.put(CONFIG_URL, json={"indicators": indicators})
    assert response.status_code == expect, response.text
    return response.json() if expect == 200 else {}  # type: ignore[no-any-return]


# ── 认证与授权 ────────────────────────────────────────────────────────


def test_indicators_requires_authentication(anon_client: TestClient) -> None:
    """未登录读行情指标 → 401。"""
    assert anon_client.get(INDICATORS_URL).status_code == 401


def test_config_requires_authentication(anon_client: TestClient) -> None:
    """未登录读/写行情配置 → 一律 401（不能只挡 GET）。"""
    assert anon_client.get(CONFIG_URL).status_code == 401
    assert anon_client.put(CONFIG_URL, json={"indicators": {}}).status_code == 401
    assert anon_client.delete(CONFIG_URL).status_code == 401


def test_config_write_requires_rule_manage(
    client: TestClient,
    db_session: Session,
    auth_as: Callable[[str, str], object],
) -> None:
    """viewer 可读配置，但写/删 → 403 且不落库。"""
    auth_as("viewer", "viewer-market-config")

    assert client.get(INDICATORS_URL).status_code == 200
    assert client.get(CONFIG_URL).status_code == 200
    payload = {"indicators": {"fut-CU": {"episode_gap": 9}}}
    assert client.put(CONFIG_URL, json=payload).status_code == 403
    assert client.delete(CONFIG_URL).status_code == 403
    assert _config_row(db_session) is None


def test_config_write_requires_csrf(
    client: TestClient,
    db_session: Session,
    auth_as: Callable[[str, str], object],
) -> None:
    """risk_admin 缺 CSRF 头 → 403，且配置行不存在（不能被绕过写入）。"""
    auth_as("risk_admin", "admin-market-config")
    client.headers.pop("X-CSRF-Token")
    payload = {"indicators": {"fut-CU": {"episode_gap": 9}}}

    assert client.put(CONFIG_URL, json=payload).status_code == 403
    assert client.delete(CONFIG_URL).status_code == 403
    assert _config_row(db_session) is None


# ── indicators 视图形状与三态 ──────────────────────────────────────────


def test_indicators_empty_database_shape(client: TestClient) -> None:
    """空库：指标齐全、观察点为 0、三态落在 accumulating、latest 全空。"""
    body = _get_indicators(client)

    assert isinstance(body["generated_at"], str)
    items = body["items"]
    assert isinstance(items, list)
    assert {entry["code"] for entry in items} == set(INDICATORS_BY_CODE)

    item = _item(body)
    assert item["label"] == INDICATORS_BY_CODE[CU_CODE].label
    assert item["source_code"] == FUTURES_SOURCE_CODE
    assert item["unit"] == INDICATORS_BY_CODE[CU_CODE].unit
    assert item["status"] == "accumulating"
    assert item["series"] == []
    assert item["latest_alert_id"] is None
    assert item["latest"] == {"observed_at": None, "value": None, "change_pct": None}
    assert item["stats"] == {
        "observations": 0,
        "sustained_change_pct": None,
        "shock_max_day_pct": None,
    }


def test_indicators_item_field_set_is_exact(client: TestClient) -> None:
    """契约字段集合精确匹配：少字段是回归，多字段（原始报文）同样是回归。"""
    body = _get_indicators(client)
    item = _item(body)

    assert set(item) == ITEM_KEYS
    assert set(item["latest"]) == LATEST_KEYS
    assert set(item["stats"]) == STATS_KEYS
    assert set(item["thresholds"]) == {
        "sustained_change_pct",
        "sustained_window",
        "shock_change_pct",
        "shock_window",
        "min_observations",
        "direction",
        "episode_gap",
        "severity_bands",
    }
    spec = INDICATORS_BY_CODE[CU_CODE]
    assert item["thresholds"]["sustained_change_pct"] == spec.sustained_change_pct
    assert item["thresholds"]["shock_change_pct"] == spec.shock_change_pct
    assert item["thresholds"]["min_observations"] == spec.min_observations
    assert item["thresholds"]["direction"] == spec.direction


def test_status_accumulating_below_min_observations(
    client: TestClient, db_session: Session
) -> None:
    """3 个观察点 < min_observations=10 且未触发 → accumulating。"""
    _series(db_session, [70000.0, 70070.0, 70140.0])

    item = _item(_get_indicators(client))
    assert item["status"] == "accumulating"
    assert item["stats"]["observations"] == 3
    assert item["latest"]["value"] == 70140.0
    assert len(item["series"]) == 3
    assert set(item["series"][0]) == SERIES_KEYS


def test_status_normal_when_below_threshold(
    client: TestClient, db_session: Session
) -> None:
    """12 个观察点、累计 +1.1%、单日 +0.1% → 观察点够但未越阈 → normal。"""
    _series(db_session, [70000.0 * (1 + 0.001 * index) for index in range(12)])

    item = _item(_get_indicators(client))
    assert item["status"] == "normal"
    assert item["stats"]["observations"] == 12
    assert 1.0 < float(item["stats"]["sustained_change_pct"]) < 1.2
    assert float(item["stats"]["shock_max_day_pct"]) == pytest.approx(0.1, abs=0.01)


def test_status_tripped_when_cumulative_change_crosses_threshold(
    client: TestClient, db_session: Session
) -> None:
    """12 个观察点累计 +8.8% ≥ 持续触发线 8% → tripped（单日未越冲击线）。"""
    _series(db_session, [70000.0 + 560.0 * index for index in range(12)])

    item = _item(_get_indicators(client))
    assert item["status"] == "tripped"
    assert float(item["stats"]["sustained_change_pct"]) >= 8.0
    assert float(item["stats"]["shock_max_day_pct"]) < 3.0


def test_series_returns_last_thirty_observations(
    client: TestClient, db_session: Session
) -> None:
    """35 个观察点 → series 只回最近 30 个且按时间升序（观测点不参与分页）。"""
    signals = _series(db_session, [70000.0 + 10.0 * index for index in range(35)])

    series = _item(_get_indicators(client))["series"]
    assert isinstance(series, list)
    assert len(series) == 30
    observed = [datetime.fromisoformat(str(point["observed_at"])) for point in series]
    assert observed == sorted(observed)
    assert observed[0] == signals[-30].collected_at
    assert float(series[-1]["value"]) == pytest.approx(70340.0)  # type: ignore[arg-type]


def test_source_name_joined_from_data_source(
    client: TestClient, db_session: Session
) -> None:
    """source_name 来自 data_sources.name；信源缺失时为 null 而不是空串。"""
    _source(db_session, FUTURES_SOURCE_CODE, "期货行情（测试改名）")
    _source(db_session, SEMICONDUCTOR_SPEC.source_code, "半导体指数（测试改名）")

    body = _get_indicators(client)
    assert _item(body)["source_name"] == "期货行情（测试改名）"
    assert _item(body, SEMICONDUCTOR_SPEC.code)["source_name"] == "半导体指数（测试改名）"

    # 信源被改名（等价于 join 落空）→ source_name 为 null
    source = db_session.scalar(
        select(DataSource).where(DataSource.code == FUTURES_SOURCE_CODE)
    )
    assert source is not None
    source.code = "commodity-futures-detached"
    db_session.flush()
    assert _item(_get_indicators(client))["source_name"] is None


def test_disabled_indicator_disappears_from_view(
    client: TestClient, db_session: Session
) -> None:
    """配置停用后：视图不再返回该指标，但配置视图仍可见且 enabled=false。"""
    _series(db_session, [70000.0 + 560.0 * index for index in range(12)])
    _put_config(client, {"fut-CU": {"enabled": False}})

    body = _get_indicators(client)
    assert CU_CODE not in {entry["code"] for entry in body["items"]}  # type: ignore[union-attr]

    config = client.get(CONFIG_URL).json()
    entry = _config_entry(config, CU_CODE)
    assert entry["enabled"] is False
    assert entry["overridden"] is True
    # 其余指标不受影响
    assert _config_entry(config, "fx-CNY")["enabled"] is True


def _config_entry(body: dict[str, object], code: str) -> dict[str, object]:
    indicators = body["indicators"]
    assert isinstance(indicators, list)
    for entry in indicators:  # type: ignore[union-attr]
        if entry["code"] == code:
            return entry  # type: ignore[no-any-return]
    raise AssertionError(f"配置响应缺少指标 {code}")


def test_config_view_reports_defaults_and_override_state(client: TestClient) -> None:
    """未配置时 source=default、overridden=false、effective 等于 defaults。"""
    body = client.get(CONFIG_URL).json()

    assert body["key"] == MARKET_CONFIG_KEY
    assert body["source"] == "default"
    assert body["updated_at"] is None
    assert {entry["code"] for entry in body["indicators"]} == set(INDICATORS_BY_CODE)

    entry = _config_entry(body, CU_CODE)
    assert set(entry) == CONFIG_INDICATOR_KEYS
    assert entry["overridden"] is False
    assert entry["effective"] == entry["defaults"]
    spec = INDICATORS_BY_CODE[CU_CODE]
    assert entry["effective"]["sustained_change_pct"] == spec.sustained_change_pct
    assert [band["threshold_pct"] for band in entry["effective"]["severity_bands"]] == [
        band.threshold_pct for band in spec.severity_bands
    ]


# ── latest_alert_id 的有 / 无两态 ──────────────────────────────────────


def test_latest_alert_id_returns_newest_current_alert(
    client: TestClient, db_session: Session
) -> None:
    """同一指标挂两条 current 提醒 → 取 id 最大（最近写入）的一条。"""
    signals = _series(db_session, [70000.0 + 560.0 * index for index in range(12)])
    older = _alert_for_signal(db_session, signals[0], suffix="old")
    newer = _alert_for_signal(db_session, signals[1], suffix="new")

    assert _item(_get_indicators(client))["latest_alert_id"] == newer.id
    assert newer.id > older.id


def test_latest_alert_id_null_when_alert_not_current(
    client: TestClient, db_session: Session
) -> None:
    """只有 expired 提醒时 latest_alert_id 必须为 null（不给已失效提醒留处置入口）。"""
    signals = _series(db_session, [70000.0 + 560.0 * index for index in range(12)])
    _alert_for_signal(db_session, signals[0], status="expired", suffix="exp")

    assert _item(_get_indicators(client))["latest_alert_id"] is None


def test_latest_alert_id_not_borrowed_from_other_indicator(
    client: TestClient, db_session: Session
) -> None:
    """提醒挂在别的指标读数上时不得张冠李戴。"""
    source = _source(db_session, FUTURES_SOURCE_CODE, "期货行情（测试）")
    other = _observation(
        db_session, source, index=900, value=1300.0, change_pct=0.2, days_ago=3
    )
    other.raw_data = {
        **other.raw_data,
        "source_extra": {"symbol": "nf_AL0", "last_price": 1300.0, "change_pct": 0.2},
    }
    db_session.flush()
    _alert_for_signal(db_session, other, suffix="al")

    _series(db_session, [70000.0 + 10.0 * index for index in range(3)])

    assert _item(_get_indicators(client))["latest_alert_id"] is None
    assert _item(_get_indicators(client), "fut-AL")["latest_alert_id"] is not None


# ── 响应不得泄露原始报文 ──────────────────────────────────────────────


def test_response_never_exposes_raw_signal_payload(
    client: TestClient, db_session: Session
) -> None:
    """视图与配置响应都不得出现 raw_data / source_extra / 指纹 / 正文原文。"""
    _series(db_session, [70000.0 + 560.0 * index for index in range(12)])
    indicators_text = client.get(INDICATORS_URL).text
    config_text = client.get(CONFIG_URL).text

    for text in (indicators_text, config_text):
        for forbidden in ("raw_data", "source_extra", "fingerprint", "dedup_key"):
            assert forbidden not in text, forbidden
    assert "沪铜主力 nf_CU0 收盘" not in indicators_text
    assert "market-api-obs-fp-" not in indicators_text


# ── 配置写入 / 回退 ──────────────────────────────────────────────────


def test_put_config_merges_and_takes_effect_immediately(
    client: TestClient, db_session: Session
) -> None:
    """两次部分 PUT 合并生效，且写入后立刻读得到新值（缓存已失效）。"""
    _put_config(client, {"fut-CU": {"sustained_change_pct": 6.0}})

    body = client.get(CONFIG_URL).json()
    assert body["source"] == "configured"
    assert body["updated_at"] is not None
    entry = _config_entry(body, CU_CODE)
    assert entry["effective"]["sustained_change_pct"] == 6.0
    assert entry["defaults"]["sustained_change_pct"] == 8.0
    assert entry["overridden"] is True

    _put_config(client, {"fut-CU": {"shock_window": 4}})

    entry = _config_entry(client.get(CONFIG_URL).json(), CU_CODE)
    assert entry["effective"]["sustained_change_pct"] == 6.0  # 合并保留
    assert entry["effective"]["shock_window"] == 4
    # 停用字段不出现在 effective 里（契约字段集合固定）
    assert set(entry["effective"]) == set(entry["defaults"])

    row = _config_row(db_session)
    assert row is not None
    assert row.enabled is True
    assert row.config["indicators"]["fut-CU"] == {
        "sustained_change_pct": 6.0,
        "shock_window": 4,
    }


def test_put_config_applies_to_indicators_view(
    client: TestClient, db_session: Session
) -> None:
    """写入的阈值立即改变指标视图的 thresholds（同一请求内不依赖 TTL）。"""
    _put_config(client, {"fut-CU": {"sustained_change_pct": 5.0, "shock_change_pct": 2.0}})

    thresholds = _item(_get_indicators(client))["thresholds"]
    assert thresholds["sustained_change_pct"] == 5.0
    assert thresholds["shock_change_pct"] == 2.0


def test_put_config_rejects_invalid_payloads(
    client: TestClient, db_session: Session
) -> None:
    """负阈值/未知指标/越界窗口/未知字段/重复分档/空标签 → 全部 422 且不落库。"""
    cases: list[dict[str, object]] = [
        {"fut-CU": {"sustained_change_pct": -5}},
        {"fut-CU": {"shock_change_pct": 51}},
        {"fut-CU": {"sustained_window": 1}},
        {"fut-CU": {"sustained_window": 61}},
        {"fut-CU": {"shock_window": 0}},
        {"fut-CU": {"min_observations": 1}},
        {"fut-CU": {"episode_gap": -1}},
        {"fut-CU": {"direction": "sideways"}},
        {"fut-CU": {"label": "   "}},
        {"fut-CU": {"source_code": "other-source"}},
        {"not-a-real-code": {"sustained_change_pct": 5}},
        {
            "fut-CU": {
                "severity_bands": [
                    {"threshold_pct": 10.0, "severity": "high"},
                    {"threshold_pct": 10.0, "severity": "medium"},
                ]
            }
        },
    ]
    for indicators in cases:
        _put_config(client, indicators, expect=422)
    assert _config_row(db_session) is None


def test_put_config_rejects_malformed_envelope(client: TestClient) -> None:
    """缺 indicators 键 / 顶层未知键 → 422。"""
    assert client.put(CONFIG_URL, json={}).status_code == 422
    assert client.put(CONFIG_URL, json={"indicators": {}, "unknown": 1}).status_code == 422
    unknown_field = {"indicators": {"fut-CU": {"unknown_field": 1}}}
    assert client.put(CONFIG_URL, json=unknown_field).status_code == 422
    assert client.put(CONFIG_URL, json={"indicators": "nope"}).status_code == 422


def test_put_config_empty_indicators_keeps_existing_overrides(
    client: TestClient, db_session: Session
) -> None:
    """空补丁是 no-op：既有覆盖不被清空（合并语义，不是整体替换）。"""
    _put_config(client, {"fut-CU": {"sustained_change_pct": 6.0}})

    _put_config(client, {})

    entry = _config_entry(client.get(CONFIG_URL).json(), CU_CODE)
    assert entry["effective"]["sustained_change_pct"] == 6.0
    assert _config_row(db_session) is not None


def test_delete_config_restores_defaults(
    client: TestClient, db_session: Session
) -> None:
    """DELETE → 204、行删除、立刻读回代码默认（缓存同步失效）。"""
    _put_config(client, {"fut-CU": {"sustained_change_pct": 6.0}})
    assert _config_entry(client.get(CONFIG_URL).json(), CU_CODE)["overridden"] is True

    response = client.delete(CONFIG_URL)
    assert response.status_code == 204, response.text
    assert _config_row(db_session) is None

    body = client.get(CONFIG_URL).json()
    assert body["source"] == "default"
    entry = _config_entry(body, CU_CODE)
    assert entry["overridden"] is False
    assert entry["effective"] == entry["defaults"]
    assert entry["effective"]["sustained_change_pct"] == 8.0


def test_delete_config_without_row_is_noop(
    client: TestClient, db_session: Session
) -> None:
    """无配置行时 DELETE 仍是 204，不产生任何副作用。"""
    assert _config_row(db_session) is None

    response = client.delete(CONFIG_URL)
    assert response.status_code == 204, response.text
    assert _config_row(db_session) is None
    assert client.get(CONFIG_URL).json()["source"] == "default"


def test_config_writes_leave_other_config_rows_untouched(
    client: TestClient, db_session: Session
) -> None:
    """行情配置不牵连同表其它维度行（如 signal-filter）。"""
    db_session.add(
        RuleDimensionConfig(
            key="signal-filter", label="信号过滤", enabled=True, config={"x": 1}
        )
    )
    db_session.flush()

    _put_config(client, {"fut-CU": {"episode_gap": 7}})
    assert client.delete(CONFIG_URL).status_code == 204

    other = db_session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == "signal-filter")
    )
    assert other is not None
    assert other.config == {"x": 1}


# ── 注册表与 schema 字面量的漂移防护 ──────────────────────────────────


def test_registry_specs_fit_response_schema() -> None:
    """注册表的 direction / severity_bands 必须能通过响应 schema 校验。"""
    adapter = TypeAdapter(IndicatorThresholdsRead)
    for spec in INDICATORS:
        payload = {
            "sustained_change_pct": spec.sustained_change_pct,
            "sustained_window": spec.sustained_window,
            "shock_change_pct": spec.shock_change_pct,
            "shock_window": spec.shock_window,
            "min_observations": spec.min_observations,
            "direction": spec.direction,
            "episode_gap": spec.episode_gap,
            "severity_bands": [
                {"threshold_pct": band.threshold_pct, "severity": band.severity}
                for band in spec.severity_bands
            ],
        }
        assert adapter.validate_python(payload) is not None, spec.code


def test_market_router_is_registered() -> None:
    """路由已挂到 /api/v1/market（否则全部用例会 404 而不是断言失败）。

    走 OpenAPI 路径表而不是 ``app.routes``：FastAPI 0.146 对 ``include_router``
    的路由做了包装，直接取 ``route.path`` 拿不到它们（会误报"未注册"）。
    """
    paths = set(app.openapi()["paths"])
    assert INDICATORS_URL in paths
    assert CONFIG_URL in paths
    assert {method for method in app.openapi()["paths"][CONFIG_URL]} >= {
        "get",
        "put",
        "delete",
    }
    assert len(INDICATORS) == len(INDICATORS_BY_CODE) == 20