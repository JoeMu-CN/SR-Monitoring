"""提醒读侧聚合（跨事件去重）纯函数测试。"""

from datetime import UTC, datetime, timedelta

from app.risks.grouping import (
    AlertGroupMember,
    event_region_keys,
    event_time_window,
    group_alert_members,
    location_region_key,
    representative,
    should_merge,
)

BASE = datetime(2026, 10, 22, 0, tzinfo=UTC)


def _member(
    alert_id: int,
    *,
    supplier_id: int = 1,
    event_type: str = "weather",
    level: str = "P3",
    start: datetime | None = None,
    end: datetime | None = None,
    regions: frozenset[str] = frozenset({"region:浙江"}),
) -> AlertGroupMember:
    return AlertGroupMember(
        alert_id=alert_id,
        supplier_id=supplier_id,
        event_type=event_type,
        event_id=alert_id,
        level=level,
        score=60,
        updated_at=BASE,
        start_at=start or BASE,
        end_at=end or BASE + timedelta(days=3),
        region_keys=regions,
    )


class TestRegionKey:
    def test_district_preferred_with_hierarchy_prefix(self) -> None:
        key = location_region_key(region="江苏省", city="苏州市", district="昆山市")
        assert key == "district:江苏省:苏州市:昆山市"

    def test_city_falls_back_when_no_district(self) -> None:
        assert location_region_key(region="江苏省", city="苏州市", district=None) == (
            "city:江苏省:苏州市"
        )

    def test_province_only_for_announcements(self) -> None:
        assert location_region_key(region="浙江省", city=None, district=None) == (
            "region:浙江省"
        )

    def test_same_name_province_and_district_do_not_collide(self) -> None:
        """河北省与天津市河北区必须得到不同键，避免跨省误并。"""
        province = location_region_key(region="河北省", city=None, district=None)
        district = location_region_key(region="天津市", city="天津市", district="河北区")
        assert province != district

    def test_no_location_yields_empty_keys(self) -> None:
        assert location_region_key(region=None, city=None, district=None) is None
        assert event_region_keys([]) == frozenset()


class TestEventWindow:
    def test_prefers_event_times(self) -> None:
        window = event_time_window(
            start_at=BASE,
            end_at=BASE + timedelta(days=2),
            valid_until=BASE + timedelta(days=5),
            created_at=BASE,
            updated_at=BASE,
        )
        assert window == (BASE, BASE + timedelta(days=2))

    def test_falls_back_to_valid_until(self) -> None:
        window = event_time_window(
            start_at=None,
            end_at=None,
            valid_until=BASE + timedelta(days=3),
            created_at=BASE,
            updated_at=BASE,
        )
        assert window == (BASE, BASE + timedelta(days=3))

    def test_naive_datetime_is_rejected(self) -> None:
        assert (
            event_time_window(
                start_at=datetime(2026, 10, 22),
                end_at=None,
                valid_until=None,
                created_at=None,
                updated_at=None,
            )
            is None
        )


class TestShouldMerge:
    def test_same_supplier_region_overlap_and_type_merges(self) -> None:
        assert should_merge(_member(1), _member(2)) is True

    def test_different_supplier_does_not_merge(self) -> None:
        assert should_merge(_member(1), _member(2, supplier_id=2)) is False

    def test_different_event_type_does_not_merge(self) -> None:
        assert should_merge(_member(1), _member(2, event_type="geological")) is False

    def test_non_overlapping_time_does_not_merge(self) -> None:
        left = _member(1, start=BASE, end=BASE + timedelta(days=1))
        right = _member(
            2, start=BASE + timedelta(days=5), end=BASE + timedelta(days=6)
        )
        assert should_merge(left, right) is False

    def test_disjoint_region_does_not_merge(self) -> None:
        left = _member(1, regions=frozenset({"region:浙江"}))
        right = _member(2, regions=frozenset({"region:江苏"}))
        assert should_merge(left, right) is False

    def test_shared_region_among_multiple_merges(self) -> None:
        """公告一稿多省时，只要有一个区域交集即可合并。"""
        left = _member(1, regions=frozenset({"region:浙江", "region:江苏"}))
        right = _member(2, regions=frozenset({"region:江苏", "region:上海"}))
        assert should_merge(left, right) is True

    def test_missing_region_never_merges(self) -> None:
        left = _member(1, regions=frozenset())
        right = _member(2)
        assert should_merge(left, right) is False


class TestGrouping:
    def test_transitive_group_of_three(self) -> None:
        groups = group_alert_members([_member(1), _member(2), _member(3)])
        assert len(groups) == 1
        assert {member.alert_id for member in groups[0]} == {1, 2, 3}

    def test_separate_suppliers_and_types(self) -> None:
        groups = group_alert_members(
            [
                _member(1),
                _member(2),
                _member(3, supplier_id=2),
                _member(4, event_type="geological"),
            ]
        )
        assert len(groups) == 3

    def test_representative_prefers_higher_level(self) -> None:
        group = [
            _member(1, level="P3"),
            _member(2, level="P1"),
            _member(3, level="P2"),
        ]
        assert representative(group).alert_id == 2

    def test_representative_same_level_prefers_latest(self) -> None:
        older = _member(1, level="P3")
        newer = AlertGroupMember(
            alert_id=2,
            supplier_id=older.supplier_id,
            event_type=older.event_type,
            event_id=2,
            level="P3",
            score=60,
            updated_at=BASE + timedelta(hours=1),
            start_at=older.start_at,
            end_at=older.end_at,
            region_keys=older.region_keys,
        )
        assert representative([older, newer]).alert_id == 2
