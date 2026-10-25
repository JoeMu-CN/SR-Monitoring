"""总览大屏补充数据：趋势补零、地理点位回退与供应商城市聚合。"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.risks.models import (
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier, SupplierSite

_LEVEL_SCORES = {"P1": 95, "P2": 80, "P3": 60, "P4": 30}


def _make_source(db_session: Session, label: str) -> DataSource:
    source = DataSource(
        code=f"ana-{label}",
        name=f"分析测试源 {label}",
        source_type="api",
        credibility=80,
        enabled=True,
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    db_session.add(source)
    db_session.flush()
    return source


def _make_supplier(
    db_session: Session,
    label: str,
    *,
    region: str | None = None,
    city: str = "上海市",
    enabled: bool = True,
    latitude: float | None = 31.23,
    longitude: float | None = 121.47,
) -> Supplier:
    supplier = Supplier(
        supplier_code=f"ANA-{label}",
        legal_name=f"分析测试供应商 {label}",
        country_code="CN",
        registry_no=None,
        enabled=enabled,
    )
    db_session.add(supplier)
    db_session.flush()
    db_session.add(
        SupplierSite(
            supplier_id=supplier.id,
            site_name=f"{label} 工厂",
            country_code="CN",
            region=region,
            city=city,
            address=f"{city}测试地址",
            latitude=latitude,
            longitude=longitude,
        )
    )
    db_session.flush()
    return supplier


def _make_alert(
    db_session: Session,
    label: str,
    supplier: Supplier,
    *,
    level: str = "P2",
    created_at: datetime | None = None,
    event_created_at: datetime | None = None,
    alert_status: str = "current",
    locations: tuple[tuple[str, str | None, str | None], ...] = (),
) -> RiskAlert:
    event = RiskEvent(
        dedup_key=f"ana-{label}",
        event_type="weather",
        event_subtype="weather_alert",
        severity="high",
        summary=f"分析测试事件 {label}",
        confidence=0.9,
        facts={},
        validity_state="active",
        validity_reason={
            "code": "effective_signal_support",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    if event_created_at is not None:
        event.created_at = event_created_at
    db_session.add(event)
    db_session.flush()
    for index, (region, country_code, city) in enumerate(locations):
        db_session.add(
            EventLocation(
                event_id=event.id,
                name=region,
                normalized_name=f"{label}-{index}",
                country_code=country_code,
                region=region,
                city=city,
            )
        )
    match = SupplierEventMatch(
        supplier_id=supplier.id,
        event_id=event.id,
        match_type="legal_name",
        score=30,
        reasons=["测试"],
        evidence=[],
    )
    db_session.add(match)
    db_session.flush()
    alert = RiskAlert(
        match_id=match.id,
        level=level,
        score=_LEVEL_SCORES[level],
        score_detail={"rule_version": "rule-v1"},
        status=alert_status,
        expires_at=None,
        expiry_kind="unbounded",
    )
    if created_at is not None:
        alert.created_at = created_at
    db_session.add(alert)
    db_session.flush()
    return alert


def _make_signal(db_session: Session, source: DataSource, alert: RiskAlert) -> None:
    match = db_session.get(SupplierEventMatch, alert.match_id)
    assert match is not None
    event_id = match.event_id
    moment = datetime.now(UTC)
    signal = RawSignal(
        source_id=source.id,
        external_id=f"ana-signal-{event_id}",
        title=f"分析测试信号 {event_id}",
        content="正文",
        published_at=moment,
        collected_at=moment,
        fingerprint=f"ana-{event_id}",
        raw_data={},
        validity_profile="weather_alert",
        validity_state="active",
        valid_from=moment,
        valid_until=moment + timedelta(days=30),
        validity_mode="fixed_days",
        lifecycle_action="assert",
        validity_policy_version="policy-ana",
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.flush()
    db_session.add(RiskEventSignal(event_id=event_id, signal_id=signal.id))
    db_session.flush()


def test_dashboard_analytics_requires_session_401(client: TestClient) -> None:
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    response = client.get("/api/v1/dashboard/analytics")

    assert response.status_code == 401


def test_dashboard_analytics_rejects_invalid_days(client: TestClient) -> None:
    for bad_days in (5, -1, "abc"):
        response = client.get("/api/v1/dashboard/analytics", params={"days": bad_days})
        assert response.status_code == 422


def test_dashboard_analytics_trend_supplier_geo_and_supplier_points(
    client: TestClient,
    db_session: Session,
) -> None:
    source = _make_source(db_session, "main")
    shanghai = _make_supplier(db_session, "SH", region="上海市", city="上海市")
    suzhou = _make_supplier(
        db_session,
        "SZ",
        region="江苏省",
        city="苏州市",
        latitude=31.30,
        longitude=120.77,
    )
    _make_supplier(
        db_session,
        "ZJ",
        region="浙江省",
        city="杭州市",
        latitude=30.27,
        longitude=120.15,
    )
    _make_supplier(
        db_session,
        "OFF",
        region="上海市",
        city="上海市",
        enabled=False,
        latitude=31.10,
        longitude=121.40,
    )

    now = datetime.now(UTC)
    zhejiang_alert = _make_alert(
        db_session,
        "zhejiang",
        shanghai,
        level="P2",
        created_at=now,
        locations=(("浙江省", "CN", None),),
    )
    taiwan_alert = _make_alert(
        db_session,
        "taiwan",
        suzhou,
        level="P4",
        created_at=now,
        locations=(("台湾省", "TW", None),),
    )
    unlocated_alert = _make_alert(
        db_session,
        "unlocated",
        shanghai,
        level="P1",
        created_at=now,
    )
    beijing_alert = _make_alert(
        db_session,
        "beijing",
        shanghai,
        level="P3",
        created_at=now,
        locations=(("", "CN", "北京市"),),
    )
    for alert in (zhejiang_alert, taiwan_alert, unlocated_alert, beijing_alert):
        _make_signal(db_session, source, alert)

    response = client.get("/api/v1/dashboard/analytics", params={"days": 30})

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 30
    assert len(payload["trend"]) == 30
    # 4 条提醒分属 4 个展示组（上海供应商 3 条因区域键不交集各自成组），全部今天入库。
    assert payload["trend"][-1]["count"] == 4
    # 所有提醒都能按供应商站点落到地图，不再以事件地点判断是否可定位。
    assert payload["unlocated_alert_count"] == 0

    by_name = {point["name"]: point for point in payload["risk_points"]}
    # 同一供应商的 3 条提醒聚合到其站点所在省；事件发生在浙江/北京不影响点位。
    assert by_name["上海市"]["supplier_count"] == 1
    assert by_name["上海市"]["site_count"] == 1
    assert by_name["上海市"]["alert_count"] == 3
    assert by_name["上海市"]["max_level"] == "P1"
    assert by_name["上海市"]["latitude"] == 31.2304
    assert by_name["上海市"]["longitude"] == 121.4737
    assert by_name["江苏省"]["max_level"] == "P4"
    # 浙江有启用供应商但没有命中提醒：仍返回点位，供前端画空心圆。
    assert by_name["浙江省"]["supplier_count"] == 1
    assert by_name["浙江省"]["alert_count"] == 0
    assert by_name["浙江省"]["max_level"] is None
    assert by_name["浙江省"]["latitude"] == 30.2741
    assert by_name["浙江省"]["longitude"] == 120.1551
    assert "台湾省" not in by_name
    assert "北京市" not in by_name

    supplier_by_city = {point["city"]: point for point in payload["supplier_points"]}
    assert supplier_by_city["上海市"]["supplier_count"] == 1
    assert supplier_by_city["苏州市"]["supplier_count"] == 1


def test_dashboard_analytics_supplier_multiple_sites_is_non_exclusive(
    client: TestClient,
    db_session: Session,
) -> None:
    source = _make_source(db_session, "multi")
    supplier = _make_supplier(db_session, "MULTI", region="上海市", city="上海市")
    db_session.add(
        SupplierSite(
            supplier_id=supplier.id,
            site_name="MULTI 苏州工厂",
            country_code="CN",
            region="江苏省",
            city="苏州市",
            address="苏州市测试地址",
            latitude=31.30,
            longitude=120.77,
        )
    )
    db_session.flush()
    alert = _make_alert(
        db_session,
        "multi",
        supplier,
        level="P3",
        created_at=datetime.now(UTC),
    )
    _make_signal(db_session, source, alert)

    response = client.get("/api/v1/dashboard/analytics", params={"days": 7})

    assert response.status_code == 200
    payload = response.json()
    points = {point["name"]: point for point in payload["risk_points"]}
    assert set(points) == {"上海市", "江苏省"}
    for point in points.values():
        assert point["supplier_count"] == 1
        assert point["site_count"] == 1
        assert point["alert_count"] == 1
        assert point["max_level"] == "P3"
    assert payload["unlocated_alert_count"] == 0


def test_dashboard_analytics_trend_counts_current_groups_once(
    client: TestClient,
    db_session: Session,
) -> None:
    """趋势只统计当前有效提醒、每组一次，并按组首次入库日归集。

    覆盖三种不该被计入的写入：规则重算留下的 expired 行、同组内的重复提醒、
    以及入库日在窗口外的历史事件。
    """
    supplier = _make_supplier(db_session, "TREND", region="上海市", city="上海市")
    now = datetime.now(UTC)
    two_days_ago = now - timedelta(days=2)
    one_day_ago = now - timedelta(days=1)
    sixty_days_ago = now - timedelta(days=60)

    # 同供应商 + 同区域 + 同类型 + 时间重叠：两条提醒合并成一组，趋势只计 1 条，
    # 日期取组内更早的事件入库日。
    _make_alert(
        db_session,
        "merge-newer",
        supplier,
        level="P3",
        event_created_at=two_days_ago,
        locations=(("上海市", "CN", "上海市"),),
    )
    _make_alert(
        db_session,
        "merge-older",
        supplier,
        level="P2",
        event_created_at=one_day_ago,
        locations=(("上海市", "CN", "上海市"),),
    )
    # 规则版本切换留下的失效行：同一批写入，不改口径会把趋势翻倍。
    _make_alert(
        db_session,
        "superseded",
        supplier,
        level="P2",
        event_created_at=now,
        alert_status="expired",
        locations=(("上海市", "CN", "上海市"),),
    )
    # 历史事件：仍是当前有效提醒，但入库日在 60 天前，不算窗口内新增。
    _make_alert(
        db_session,
        "historic",
        supplier,
        level="P3",
        event_created_at=sixty_days_ago,
        locations=(("北京市", "CN", "北京市"),),
    )

    response = client.get("/api/v1/dashboard/analytics", params={"days": 7})

    assert response.status_code == 200
    trend = response.json()["trend"]
    assert len(trend) == 7
    counts = {point["date"]: point["count"] for point in trend}
    assert counts[two_days_ago.date().isoformat()] == 1
    assert counts[one_day_ago.date().isoformat()] == 0
    # 7 天窗口合计 = 窗口内首次出现的组数；历史组与失效行都不计。
    assert sum(counts.values()) == 1

    summary = client.get("/api/v1/dashboard/summary", params={"days": 7}).json()
    # 当前总数含历史组（2 组），因此窗口新增 1 条小于当前总数，两者不再互相冒充。
    assert summary["total_current"] == 2


def test_dashboard_analytics_trend_matches_total_current_when_all_groups_are_recent(
    client: TestClient,
    db_session: Session,
) -> None:
    """窗口覆盖全部当前组时，趋势合计与 KPI 的 total_current 同口径同数值。"""
    supplier = _make_supplier(db_session, "SAME", region="上海市", city="上海市")
    now = datetime.now(UTC)
    for index in range(3):
        _make_alert(
            db_session,
            f"recent-{index}",
            supplier,
            level="P3",
            event_created_at=now - timedelta(days=index),
            locations=((f"测试区域{index}", "CN", f"测试城市{index}"),),
        )

    analytics = client.get("/api/v1/dashboard/analytics", params={"days": 7}).json()
    summary = client.get("/api/v1/dashboard/summary", params={"days": 7}).json()

    assert sum(point["count"] for point in analytics["trend"]) == summary["total_current"]
    assert summary["total_current"] == 3
