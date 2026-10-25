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
        status="current",
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
    assert payload["trend"][-1]["count"] >= 3
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
