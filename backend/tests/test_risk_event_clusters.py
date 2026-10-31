"""风险事件簇（事件级聚合）与事件级口径契约测试。

覆盖：同类型/地区相交/时间窗重叠才聚类、缺地区不合并、事件按 event_id 去重、
active / partially_expired / expired 三态、分页与排序、已失效实时判活与失效原因，
以及 /risk-event-clusters/summary 与 /risk-alerts/summary、/dashboard/summary 的
同一 as_of 口径自洽。夹具数据自造，数值不要求等于生产库。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.risks.models import (
    EventLocation,
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

NOW = datetime.now(UTC)
DAY = timedelta(days=1)


def _source(db_session: Session, code: str) -> DataSource:
    existing = db_session.scalar(select(DataSource).where(DataSource.code == code))
    if existing is not None:
        return existing
    source = DataSource(
        code=code,
        name=f"{code} 信源",
        source_type="api",
        credibility=90,
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


def _event(
    db_session: Session,
    key: str,
    *,
    event_type: str = "weather",
    summary: str | None = None,
    region: str | None = "浙江省",
    city: str | None = "宁波市",
    district: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    validity_state: str = "active",
    source_code: str = "cluster-src",
    published_at: datetime | None = None,
) -> RiskEvent:
    """创建带一条有效信号的事件；地点为空时该事件不参与聚类。"""
    source = _source(db_session, source_code)
    published = published_at or NOW
    signal = RawSignal(
        source_id=source.id,
        external_id=key,
        title=f"{key} 信号",
        content=f"{key} 内容",
        published_at=published,
        collected_at=published,
        fingerprint=f"fp-{key}",
        raw_data={},
        validity_profile="weather_alert",
        validity_state="active",
        valid_from=published,
        valid_until=None,
        review_due_at=None,
        validity_mode="until_revoked",
        lifecycle_action="assert",
        validity_policy_version=f"policy-{key}",
        validity_reason={
            "code": "policy_resolved",
            "anchor_source": "published_at",
            "details": {},
        },
    )
    db_session.add(signal)
    db_session.flush()
    event = RiskEvent(
        dedup_key=key,
        event_type=event_type,
        event_subtype=None,
        severity="high",
        summary=summary or f"{key} 事件摘要",
        start_at=start_at,
        end_at=end_at,
        confidence=0.9,
        facts={},
        validity_state=validity_state,
        validity_reason=(
            {
                "code": "effective_signal_support",
                "anchor_source": "published_at",
                "details": {},
            }
            if validity_state != "expired"
            else {
                "code": "no_effective_signal_support",
                "anchor_source": "published_at",
                "details": {"effective_count": 0},
            }
        ),
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(RiskEventSignal(event_id=event.id, signal_id=signal.id))
    if region or city or district:
        db_session.add(
            EventLocation(
                event_id=event.id,
                name=district or city or region or "",
                normalized_name=f"loc-{key}",
                country_code="CN",
                region=region,
                city=city,
                district=district,
            )
        )
    db_session.flush()
    return event


def _supplier(db_session: Session, code: str, name: str | None = None) -> Supplier:
    existing = db_session.scalar(select(Supplier).where(Supplier.supplier_code == code))
    if existing is not None:
        return existing
    supplier = Supplier(
        supplier_code=code,
        legal_name=name or f"{code} 供应商",
        country_code="CN",
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


def _alert(
    db_session: Session,
    event: RiskEvent,
    supplier: Supplier,
    *,
    level: str = "P3",
    score: int = 60,
    status: str = "current",
    expires_at: datetime | None = None,
    rule_version: str = "rule-v1",
) -> RiskAlert:
    """为 (event, supplier) 建一条提醒；同一组合的多版本提醒复用同一匹配行。"""
    match = db_session.scalar(
        select(SupplierEventMatch).where(
            SupplierEventMatch.event_id == event.id,
            SupplierEventMatch.supplier_id == supplier.id,
        )
    )
    if match is None:
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
        score=score,
        score_detail={"rule_version": rule_version},
        status=status,
        expires_at=expires_at,
        expiry_kind="unbounded" if expires_at is None else "finite",
    )
    db_session.add(alert)
    db_session.flush()
    return alert


def _clusters(
    client: TestClient, *, state: str = "current", **params: int
) -> dict[str, Any]:
    response = client.get(
        "/api/v1/risk-event-clusters", params={"state": state, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _cluster_by_region(payload: dict[str, Any], label: str) -> dict[str, Any]:
    for item in payload["items"]:
        if item["region_label"] == label:
            return item
    raise AssertionError(f"未找到地区为 {label} 的事件簇")


def _as_datetime(value: str) -> datetime:
    """响应时间串可能是 Z 结尾或 +00:00，统一解析后再比较。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TestClusterMerging:
    def test_merges_same_type_region_and_overlap_across_sources(
        self, client: TestClient, db_session: Session
    ) -> None:
        """同类型 + 地区相交 + 时间窗重叠跨信源合并；事件按 event_id 去重。"""
        supplier_a = _supplier(db_session, "CLU-A")
        supplier_b = _supplier(db_session, "CLU-B")
        supplier_c = _supplier(db_session, "CLU-C")
        first = _event(
            db_session,
            "clu-weather-1",
            start_at=NOW,
            end_at=NOW + 5 * DAY,
            source_code="clu-src-a",
        )
        second = _event(
            db_session,
            "clu-weather-2",
            start_at=NOW + DAY,
            end_at=NOW + 3 * DAY,
            source_code="clu-src-b",
            published_at=NOW + timedelta(hours=2),
        )
        third = _event(
            db_session,
            "clu-weather-3",
            start_at=NOW + 2 * DAY,
            end_at=NOW + 4 * DAY,
            source_code="clu-src-a",
        )
        # 同省不同市、不同省、不同类型、无地点：都不应与上面的簇合并。
        other_city = _event(
            db_session,
            "clu-other-city",
            city="杭州市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        other_province = _event(
            db_session,
            "clu-other-province",
            region="江苏省",
            city="苏州市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        other_type = _event(
            db_session,
            "clu-other-type",
            event_type="geological",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        no_location = _event(
            db_session,
            "clu-no-location",
            region=None,
            city=None,
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        # first 关联两个供应商（验证同一事件只产生一个成员）。
        _alert(db_session, first, supplier_a, level="P2", expires_at=NOW + 5 * DAY)
        _alert(db_session, first, supplier_b, expires_at=NOW + 4 * DAY)
        _alert(db_session, second, supplier_c, expires_at=NOW + 3 * DAY)
        _alert(db_session, third, supplier_c, expires_at=NOW + 2 * DAY)
        for event in (other_city, other_province, other_type, no_location):
            _alert(db_session, event, supplier_a)
        db_session.commit()

        payload = _clusters(client)
        cluster = _cluster_by_region(payload, "浙江省宁波市")

        assert payload["total"] == 5
        assert cluster["event_count"] == 3
        member_ids = [member["event_id"] for member in cluster["members"]]
        assert member_ids == [first.id, second.id, third.id]
        assert len(member_ids) == len(set(member_ids))
        assert cluster["supplier_count"] == 3
        assert cluster["max_level"] == "P2"
        assert {
            entry["level"]: entry["count"] for entry in cluster["level_counts"]
        } == {"P2": 1, "P3": 2}
        assert sum(entry["count"] for entry in cluster["level_counts"]) == 3
        # 同一事件多供应商：成员级截止取最早，簇级给出 min/max 范围。
        first_member = next(
            member for member in cluster["members"] if member["event_id"] == first.id
        )
        assert first_member["supplier_count"] == 2
        assert first_member["max_level"] == "P2"
        assert _as_datetime(first_member["expires_at"]) == NOW + 4 * DAY
        assert _as_datetime(cluster["expires_min"]) == NOW + 2 * DAY
        assert _as_datetime(cluster["expires_max"]) == NOW + 5 * DAY
        # 成员来源取每个事件最新信号的信源，跨信源仍在一个簇内。
        member_sources = {
            member["event_id"]: member["source_code"] for member in cluster["members"]
        }
        assert member_sources[first.id] == "clu-src-a"
        assert member_sources[second.id] == "clu-src-b"
        assert cluster["status"] == "active"
        assert cluster["cluster_id"] == (
            f"weather:浙江省宁波市:{NOW.date().isoformat()}"
        )

    def test_missing_region_or_non_overlapping_window_never_merges(
        self, client: TestClient, db_session: Session
    ) -> None:
        supplier = _supplier(db_session, "CLU-NOMERGE")
        early = _event(
            db_session,
            "clu-early",
            start_at=NOW,
            end_at=NOW + DAY,
        )
        late = _event(
            db_session,
            "clu-late",
            start_at=NOW + 10 * DAY,
            end_at=NOW + 11 * DAY,
        )
        no_region = _event(
            db_session,
            "clu-no-region",
            region=None,
            city=None,
            start_at=NOW,
            end_at=NOW + DAY,
        )
        for event in (early, late, no_region):
            _alert(db_session, event, supplier)
        db_session.commit()

        payload = _clusters(client)

        assert payload["total"] == 3
        assert all(cluster["event_count"] == 1 for cluster in payload["items"])
        labels = {
            (cluster["event_type"], cluster["region_label"])
            for cluster in payload["items"]
        }
        assert ("weather", "未标注地区") in labels

    def test_cluster_id_is_stable_across_repeated_requests(
        self, client: TestClient, db_session: Session
    ) -> None:
        event = _event(
            db_session,
            "clu-stable",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(db_session, event, _supplier(db_session, "CLU-STABLE"))
        db_session.commit()

        first = _clusters(client)["items"]
        second = _clusters(client)["items"]

        assert [item["cluster_id"] for item in first] == [
            item["cluster_id"] for item in second
        ]


class TestClusterStatus:
    def test_partially_expired_member_marks_cluster_partial(
        self, client: TestClient, db_session: Session
    ) -> None:
        supplier_a = _supplier(db_session, "CLU-PART-A")
        supplier_b = _supplier(db_session, "CLU-PART-B")
        live = _event(
            db_session,
            "clu-part-live",
            start_at=NOW,
            end_at=NOW + 3 * DAY,
        )
        _alert(db_session, live, supplier_a)
        mixed = _event(
            db_session,
            "clu-part-mixed",
            start_at=NOW,
            end_at=NOW + 3 * DAY,
        )
        _alert(db_session, mixed, supplier_a)
        _alert(db_session, mixed, supplier_b, status="expired")
        db_session.commit()

        cluster = _cluster_by_region(_clusters(client), "浙江省宁波市")

        assert cluster["event_count"] == 2
        assert cluster["status"] == "partially_expired"
        mixed_member = next(
            member for member in cluster["members"] if member["event_id"] == mixed.id
        )
        # 仍有当前有效提醒：供应商数与等级取有效提醒口径。
        assert mixed_member["supplier_count"] == 1
        assert mixed_member["expired_reason"] is not None

    def test_fully_expired_event_forms_expired_cluster(
        self, client: TestClient, db_session: Session
    ) -> None:
        event = _event(
            db_session,
            "clu-dead",
            region="江苏省",
            city="苏州市",
            validity_state="expired",
            start_at=NOW,
            end_at=NOW + 3 * DAY,
        )
        _alert(db_session, event, _supplier(db_session, "CLU-DEAD"), status="expired")
        db_session.commit()

        expired = _cluster_by_region(_clusters(client, state="expired"), "江苏省苏州市")

        assert expired["status"] == "expired"
        assert expired["event_count"] == 1
        assert expired["members"][0]["expired_reason"] == "event_expired"
        # 已失效事件不出现在当前口径里。
        assert _clusters(client)["total"] == 0


class TestExpiredClusterReasons:
    def _seed_expired_variants(self, db_session: Session) -> dict[str, int]:
        event_expired = _event(
            db_session,
            "reason-event",
            region="江苏省",
            city="苏州市",
            validity_state="expired",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(
            db_session,
            event_expired,
            _supplier(db_session, "CLU-REASON-A"),
            status="expired",
        )

        alert_expired = _event(
            db_session,
            "reason-alert",
            region="山东省",
            city="青岛市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        # status 仍是 current，但截止时间已过：判活必须看 SQL 谓词而不是 status。
        _alert(
            db_session,
            alert_expired,
            _supplier(db_session, "CLU-REASON-B"),
            expires_at=NOW - DAY,
        )

        superseded = _event(
            db_session,
            "reason-superseded",
            region="广东省",
            city="深圳市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        supplier = _supplier(db_session, "CLU-REASON-C")
        _alert(
            db_session,
            superseded,
            supplier,
            level="P3",
            status="expired",
            rule_version="rule-v1",
        )
        _alert(
            db_session,
            superseded,
            supplier,
            level="P2",
            status="current",
            rule_version="rule-v2",
        )
        db_session.commit()
        return {
            "event_expired": event_expired.id,
            "alert_expired": alert_expired.id,
            "version_superseded": superseded.id,
        }

    def test_expired_clusters_report_reasons(
        self, client: TestClient, db_session: Session
    ) -> None:
        event_ids = self._seed_expired_variants(db_session)

        payload = _clusters(client, state="expired")
        reasons: dict[int, str | None] = {}
        statuses: dict[int, str] = {}
        for cluster in payload["items"]:
            for member in cluster["members"]:
                reasons[member["event_id"]] = member["expired_reason"]
                statuses[member["event_id"]] = cluster["status"]

        assert reasons[event_ids["event_expired"]] == "event_expired"
        assert reasons[event_ids["alert_expired"]] == "alert_expired"
        assert reasons[event_ids["version_superseded"]] == "version_superseded"
        assert statuses[event_ids["event_expired"]] == "expired"
        assert statuses[event_ids["alert_expired"]] == "expired"
        # 旧版本被新版本取代，但事件仍有当前有效提醒：已失效视图标记部分失效。
        assert statuses[event_ids["version_superseded"]] == "partially_expired"

        current_regions = {
            item["region_label"] for item in _clusters(client)["items"]
        }
        assert current_regions == {"广东省深圳市"}

    def test_version_superseded_member_appears_once(
        self, client: TestClient, db_session: Session
    ) -> None:
        """同一 event×supplier 的两个版本只产生一个成员事件（event_id 去重）。"""
        event_ids = self._seed_expired_variants(db_session)

        members = [
            member
            for cluster in _clusters(client, state="expired")["items"]
            for member in cluster["members"]
            if member["event_id"] == event_ids["version_superseded"]
        ]

        assert len(members) == 1


class TestClusterPaginationAndSorting:
    def _seed_three_clusters(self, db_session: Session) -> None:
        supplier = _supplier(db_session, "CLU-SORT")
        top = _event(
            db_session,
            "sort-p1",
            region="江苏省",
            city="苏州市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(db_session, top, supplier, level="P1")
        later = _event(
            db_session,
            "sort-p3-later",
            region="山东省",
            city="青岛市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(db_session, later, supplier, level="P3", expires_at=NOW + 3 * DAY)
        second = _event(
            db_session,
            "sort-p3-later-2",
            region="山东省",
            city="青岛市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(db_session, second, supplier, level="P3", expires_at=NOW + 3 * DAY)
        earlier = _event(
            db_session,
            "sort-p3-earlier",
            region="广东省",
            city="深圳市",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(
            db_session,
            earlier,
            supplier,
            level="P3",
            expires_at=NOW + timedelta(hours=1),
        )
        db_session.commit()

    def test_sorts_by_level_then_earliest_expiry_then_event_count(
        self, client: TestClient, db_session: Session
    ) -> None:
        self._seed_three_clusters(db_session)

        payload = _clusters(client)
        regions = [item["region_label"] for item in payload["items"]]

        # P1 在前；同为 P3 时最早截止在前；山东簇有 2 个事件。
        assert regions == ["江苏省苏州市", "广东省深圳市", "山东省青岛市"]
        assert payload["items"][2]["event_count"] == 2

    def test_pagination_slices_after_sorting(
        self, client: TestClient, db_session: Session
    ) -> None:
        self._seed_three_clusters(db_session)

        first_page = _clusters(client, limit=2, offset=0)
        second_page = _clusters(client, limit=2, offset=2)

        assert first_page["total"] == second_page["total"] == 3
        assert len(first_page["items"]) == 2
        assert len(second_page["items"]) == 1
        assert first_page["limit"] == 2
        assert second_page["offset"] == 2

    def test_rejects_invalid_state_and_out_of_range_limit(
        self, client: TestClient
    ) -> None:
        bad_state = client.get("/api/v1/risk-event-clusters", params={"state": "bogus"})
        bad_limit = client.get("/api/v1/risk-event-clusters", params={"limit": 101})

        assert bad_state.status_code == 422
        assert bad_limit.status_code == 422


class TestClusterSummaryConsistency:
    def _seed(self, db_session: Session) -> None:
        supplier_a = _supplier(db_session, "CLU-SUM-A", "阿尔法供应商")
        supplier_b = _supplier(db_session, "CLU-SUM-B", "贝塔供应商")
        first = _event(
            db_session,
            "sum-1",
            start_at=NOW,
            end_at=NOW + 3 * DAY,
            source_code="clu-sum-src-a",
        )
        second = _event(
            db_session,
            "sum-2",
            start_at=NOW + DAY,
            end_at=NOW + 4 * DAY,
            source_code="clu-sum-src-b",
            published_at=NOW + timedelta(hours=1),
        )
        _alert(db_session, first, supplier_a, level="P1", expires_at=NOW + 3 * DAY)
        _alert(db_session, second, supplier_a, level="P3", expires_at=NOW + DAY)
        _alert(db_session, first, supplier_b, level="P2", expires_at=NOW + 2 * DAY)
        db_session.commit()

    def test_cluster_summary_matches_alert_and_dashboard_summary(
        self, client: TestClient, db_session: Session
    ) -> None:
        self._seed(db_session)

        cluster_summary = client.get("/api/v1/risk-event-clusters/summary")
        alert_summary = client.get("/api/v1/risk-alerts/summary")
        dashboard = client.get("/api/v1/dashboard/summary", params={"days": 30})

        assert cluster_summary.status_code == 200, cluster_summary.text
        assert alert_summary.status_code == 200, alert_summary.text
        assert dashboard.status_code == 200, dashboard.text
        cluster_payload = cluster_summary.json()
        alert_payload = alert_summary.json()
        # 2 个事件、1 个簇、2 个风险组、3 条当前提醒、2 个供应商。
        assert cluster_payload["current_events"] == 2
        assert cluster_payload["current_clusters"] == 1
        assert cluster_payload["current_groups"] == 2
        assert cluster_payload["current_alerts"] == 3
        assert cluster_payload["current_suppliers"] == 2
        assert cluster_payload["as_of"]
        # 与提醒汇总、总览同一 as_of 口径自洽。
        assert alert_payload["total_current"] == cluster_payload["current_groups"]
        assert alert_payload["total_events"] == cluster_payload["current_events"]
        assert alert_payload["total_suppliers"] == cluster_payload["current_suppliers"]
        assert alert_payload["total_clusters"] == cluster_payload["current_clusters"]
        assert dashboard.json()["total_current"] == cluster_payload["current_groups"]
        # 列表口径与汇总一致。
        listing = _clusters(client)
        assert listing["total"] == cluster_payload["current_clusters"]
        assert listing["items"][0]["event_count"] == 2
        assert listing["items"][0]["supplier_count"] == 2

    def test_cluster_summary_zero_contract_without_alerts(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/risk-event-clusters/summary")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["current_events"] == 0
        assert payload["current_clusters"] == 0
        assert payload["current_groups"] == 0
        assert payload["current_alerts"] == 0
        assert payload["current_suppliers"] == 0
        assert payload["as_of"]


class TestRiskAlertEventFields:
    def _seed_group(self, db_session: Session) -> None:
        supplier_a = _supplier(db_session, "CLU-FIELD-A", "阿尔法供应商")
        supplier_b = _supplier(db_session, "CLU-FIELD-B", "贝塔供应商")
        first = _event(
            db_session,
            "field-1",
            start_at=NOW,
            end_at=NOW + 3 * DAY,
            source_code="clu-field-src-a",
        )
        second = _event(
            db_session,
            "field-2",
            start_at=NOW + DAY,
            end_at=NOW + 4 * DAY,
            source_code="clu-field-src-b",
            published_at=NOW + timedelta(hours=1),
        )
        _alert(db_session, first, supplier_a, level="P1", expires_at=NOW + 3 * DAY)
        _alert(db_session, second, supplier_a, level="P3", expires_at=NOW + DAY)
        _alert(db_session, first, supplier_b, level="P2", expires_at=NOW + 2 * DAY)
        db_session.commit()

    def test_alert_list_exposes_group_and_merged_event_fields(
        self, client: TestClient, db_session: Session
    ) -> None:
        self._seed_group(db_session)

        response = client.get("/api/v1/risk-alerts", params={"status": "current"})

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        grouped = next(item for item in items if item["group_event_count"] == 2)
        assert grouped["group_status"] == "active"
        assert grouped["group_event_count"] == 2
        assert grouped["group_source_count"] == 2
        assert _as_datetime(grouped["group_expires_min"]) == NOW + DAY
        assert _as_datetime(grouped["group_expires_max"]) == NOW + 3 * DAY
        assert grouped["merged_count"] == 2
        merged = grouped["merged_events"][0]
        assert merged["status"] == "current"
        assert merged["version_superseded"] is False
        assert merged["supplier_count"] == 1
        assert merged["validity_state"] == "active"
        assert merged["valid_from"]
        assert merged["expires_at"]

    def test_expired_alert_rows_carry_expired_group_status(
        self, client: TestClient, db_session: Session
    ) -> None:
        event = _event(
            db_session,
            "field-expired",
            validity_state="expired",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        _alert(
            db_session,
            event,
            _supplier(db_session, "CLU-FIELD-EXPIRED"),
            status="expired",
        )
        db_session.commit()

        response = client.get("/api/v1/risk-alerts", params={"status": "expired"})

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["group_status"] == "expired"
        assert item["group_event_count"] == 1
        assert item["group_expires_min"] is None
        assert item["group_expires_max"] is None

    def test_event_detail_lists_suppliers_sorted_by_level(
        self, client: TestClient, db_session: Session
    ) -> None:
        event = _event(
            db_session,
            "field-detail",
            start_at=NOW,
            end_at=NOW + 2 * DAY,
        )
        supplier_low = _supplier(db_session, "CLU-DETAIL-LOW", "贝塔供应商")
        supplier_high = _supplier(db_session, "CLU-DETAIL-HIGH", "阿尔法供应商")
        _alert(db_session, event, supplier_low, level="P3")
        # 旧版本 expired + 新版本 current：详情只展示当前版本，不重复列供应商。
        _alert(
            db_session,
            event,
            supplier_high,
            level="P4",
            status="expired",
            rule_version="rule-v1",
        )
        _alert(
            db_session,
            event,
            supplier_high,
            level="P1",
            status="current",
            expires_at=NOW + 2 * DAY,
            rule_version="rule-v2",
        )
        db_session.commit()

        response = client.get(f"/api/v1/events/{event.id}")

        assert response.status_code == 200, response.text
        suppliers = response.json()["suppliers"]
        assert [row["supplier_id"] for row in suppliers] == [
            supplier_high.id,
            supplier_low.id,
        ]
        assert [row["level"] for row in suppliers] == ["P1", "P3"]
        assert suppliers[0]["alert_status"] == "current"
        assert suppliers[0]["version_superseded"] is False
        assert suppliers[0]["alert_id"] is not None
        assert _as_datetime(suppliers[0]["expires_at"]) == NOW + 2 * DAY
        assert suppliers[1]["expires_at"] is None
