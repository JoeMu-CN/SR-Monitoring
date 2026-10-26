"""总览汇总聚合：当前统计与期间新增的明确口径。

当前统计与趋势图全部共享同一 as_of 的 current_alert_condition 和同一读侧
分组去重；期间新增 ``period_new_count`` 仍统计窗口内创建的全部提醒行（含已
失效、含规则版本重算副本），仅用于运维侧写入量核对。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session

from app.config import get_retention_settings
from app.risks.grouping_queries import (
    AlertRow,
    current_alert_rows,
    representative_rows,
)
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    SupplierEventMatch,
)
from app.risks.query_validity import current_alert_condition
from app.risks.schemas import (
    DashboardAnalytics,
    EventTypeCount,
    GeoRiskPoint,
    GeoSupplierPoint,
    LevelCount,
    SourceDistributionItem,
    TrendPoint,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier, SupplierSite

RECENT_ALERT_LIMIT: Final = 10
TODAY_LOOKBACK: Final = timedelta(days=1)
CURRENT_LEVELS: Final = ("P1", "P2", "P3", "P4")
LEVEL_ORDER: Final[dict[str, int]] = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}

# 省级行政区中心点（经度, 纬度）。供应商站点按省聚合后回退到省级中心点；
# 仅用于总览态势展示，不作为精确地理定位。
PROVINCE_CENTROIDS: Final[dict[str, tuple[float, float]]] = {
    "北京": (116.4074, 39.9042),
    "天津": (117.2008, 39.0842),
    "河北": (114.5149, 38.0428),
    "山西": (112.5492, 37.8570),
    "内蒙古": (111.7492, 40.8426),
    "辽宁": (123.4315, 41.8057),
    "吉林": (125.3235, 43.8171),
    "黑龙江": (126.6425, 45.7567),
    "上海": (121.4737, 31.2304),
    "江苏": (118.7969, 32.0603),
    "浙江": (120.1551, 30.2741),
    "安徽": (117.2272, 31.8206),
    "福建": (119.2965, 26.0745),
    "江西": (115.8582, 28.6820),
    "山东": (117.1205, 36.6510),
    "河南": (113.6254, 34.7466),
    "湖北": (114.3054, 30.5931),
    "湖南": (112.9388, 28.2282),
    "广东": (113.2644, 23.1291),
    "广西": (108.3200, 22.8240),
    "海南": (110.3312, 20.0311),
    "重庆": (106.5516, 29.5630),
    "四川": (104.0665, 30.5723),
    "贵州": (106.6302, 26.6477),
    "云南": (102.8329, 24.8801),
    "西藏": (91.1409, 29.6456),
    "陕西": (108.9398, 34.3416),
    "甘肃": (103.8343, 36.0611),
    "青海": (101.7782, 36.6171),
    "宁夏": (106.2782, 38.4664),
    "新疆": (87.6168, 43.8256),
    "台湾": (121.5654, 25.0330),
    "香港": (114.1694, 22.3193),
    "澳门": (113.5439, 22.1987),
}

# 供应商站点没有省级中心点时按城市名回退。只覆盖高频贸易城市，
# 避免把只有城市名的站点画到国家几何中心。
CITY_CENTROIDS: Final[dict[str, tuple[float, float]]] = {
    "北京": (116.4074, 39.9042),
    "天津": (117.2008, 39.0842),
    "上海": (121.4737, 31.2304),
    "重庆": (106.5516, 29.5630),
    "苏州": (120.5853, 31.2989),
    "无锡": (120.3119, 31.4912),
    "宁波": (121.5503, 29.8746),
    "常州": (119.9741, 31.8112),
    "烟台": (121.4479, 37.4638),
    "盐城": (120.1636, 33.3477),
    "湖州": (120.0868, 30.8943),
    "台北": (121.5654, 25.0330),
    "深圳": (114.0579, 22.5431),
    "广州": (113.2644, 23.1291),
    "杭州": (120.1551, 30.2741),
    "南京": (118.7969, 32.0603),
    "青岛": (120.3826, 36.0671),
    "厦门": (118.0894, 24.4798),
    "福州": (119.2965, 26.0745),
    "合肥": (117.2272, 31.8206),
    "武汉": (114.3054, 30.5931),
    "成都": (104.0665, 30.5723),
}

# 国家级回退点：供应商站点既无省级中心点、城市中心点，也无有效经纬度时使用。
# 未覆盖的国家不计入地图点位，避免把点位错误投放到海里。
COUNTRY_CENTROIDS: Final[dict[str, tuple[float, float]]] = {
    "CN": (104.1954, 35.8617),
    "JP": (138.2529, 36.2048),
    "TW": (121.0000, 23.7000),
    "KR": (127.7669, 35.9078),
    "SG": (103.8198, 1.3521),
    "VN": (108.2772, 14.0583),
    "TH": (100.9925, 15.8700),
    "MY": (101.9758, 4.2105),
    "ID": (113.9213, -0.7893),
    "PH": (121.7740, 12.8797),
    "IN": (78.9629, 20.5937),
    "PK": (69.3451, 30.3753),
    "BD": (90.3563, 23.6850),
    "LK": (80.7718, 7.8731),
    "MM": (95.9560, 21.9140),
    "KH": (104.9910, 12.5657),
    "LA": (102.4955, 19.8563),
    "MN": (103.8467, 46.8625),
    "KZ": (66.9237, 48.0196),
    "UZ": (64.5853, 41.3775),
    "US": (-95.7129, 37.0902),
    "CA": (-106.3468, 56.1304),
    "MX": (-102.5528, 23.6345),
    "BR": (-51.9253, -14.2350),
    "AR": (-63.6167, -38.4161),
    "CL": (-71.5429, -35.6751),
    "PE": (-75.0152, -9.1900),
    "CO": (-74.2973, 4.5709),
    "GB": (-3.4360, 55.3781),
    "DE": (10.4515, 51.1657),
    "NL": (5.2913, 52.1326),
    "FR": (2.2137, 46.2276),
    "IT": (12.5674, 41.8719),
    "ES": (-3.7492, 40.4637),
    "PT": (-8.2245, 39.3999),
    "BE": (4.4699, 50.5039),
    "CH": (8.2275, 46.8182),
    "AT": (14.5501, 47.5162),
    "PL": (19.1451, 51.9194),
    "SE": (18.6435, 60.1282),
    "NO": (8.4689, 60.4720),
    "FI": (25.7482, 61.9241),
    "DK": (9.5018, 56.2639),
    "RU": (105.3188, 61.5240),
    "UA": (31.1656, 48.3794),
    "TR": (35.2433, 38.9637),
    "AE": (53.8478, 23.4241),
    "SA": (45.0792, 23.8859),
    "IR": (53.6880, 32.4279),
    "IQ": (43.6793, 33.2232),
    "IL": (34.8516, 31.0461),
    "EG": (30.8025, 26.8206),
    "NG": (8.6753, 9.0820),
    "ZA": (22.9375, -30.5595),
    "AU": (133.7751, -25.2744),
    "NZ": (174.8860, -40.9006),
    "PR": (-66.5901, 18.2208),
    "PA": (-80.7821, 8.5380),
    "VU": (166.9592, -15.3767),
    "PG": (143.9555, -6.3149),
    "DO": (-70.1627, 18.7357),
    "FJ": (178.0650, -17.7134),
    "TO": (-175.1982, -21.1790),
    "VI": (-64.8963, 18.3358),
    "TL": (125.7275, -8.8742),
    "MG": (46.8691, -18.7669),
    "MP": (145.6746, 15.0979),
    "EC": (-78.1834, -1.8312),
    "RO": (24.9668, 45.9432),
    "GR": (21.8243, 39.0742),
    "CZ": (15.4730, 49.8175),
    "HU": (19.5033, 47.1625),
}

RecentAlertRow = Row[tuple[RiskAlert, SupplierEventMatch, RiskEvent, Supplier]]
RepresentativeRow = tuple[RiskAlert, SupplierEventMatch, RiskEvent, Supplier]


@dataclass(frozen=True, slots=True)
class DashboardAggregate:
    """单次 as_of 下的全部汇总值与最近提醒原始行。"""

    as_of: datetime
    window_start: datetime
    window_days: int
    level_counts: list[LevelCount]
    total_current: int
    today_new: int
    type_distribution: list[EventTypeCount]
    period_new_count: int
    supplier_total: int
    active_supplier_total: int
    source_distribution: list[SourceDistributionItem]
    retention_window_days: int
    history_may_be_partial: bool
    recent_rows: Sequence[RecentAlertRow]


def utc_now() -> datetime:
    """返回当前 UTC 时间；独立函数以便测试冻结时钟。"""
    return datetime.now(UTC)


def build_dashboard_aggregate(
    session: Session, *, days: int, now_utc: datetime | None = None
) -> DashboardAggregate:
    """在同一 as_of 下完成全部只读聚合；调用方不得中途提交写入。

    前置条件：days 已在边界解析为 7/30/90 之一（路由层以 422 拒绝非法值）。
    """
    as_of = now_utc if now_utc is not None else utc_now()
    window_start = as_of - timedelta(days=days)
    current_condition = current_alert_condition(as_of)

    # 与提醒列表共用同一读侧聚合：概览计数按"合并后的组"统计，避免列表 44 条、
    # 概览 70 条的口径不一致。
    representatives, _groups = representative_rows(
        session, current_alert_rows(session, now_utc=as_of)
    )
    level_map: dict[str, int] = {}
    for alert, _match, _event, _supplier in representatives:
        level_map[alert.level] = level_map.get(alert.level, 0) + 1
    level_counts = [
        LevelCount(level=level, count=level_map.get(level, 0))
        for level in CURRENT_LEVELS
    ]
    total_current = sum(level_map.values())

    today_new = (
        session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(
                current_condition,
                RiskAlert.created_at >= as_of - TODAY_LOOKBACK,
                RiskAlert.created_at < as_of,
            )
        )
        or 0
    )

    type_map: dict[str, int] = {}
    for _alert, _match, event, _supplier in representatives:
        type_map[event.event_type] = type_map.get(event.event_type, 0) + 1
    type_distribution = [
        EventTypeCount(event_type=event_type, count=count)
        for event_type, count in sorted(type_map.items(), key=lambda item: (-item[1], item[0]))
    ]

    period_new_count = (
        session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .where(
                RiskAlert.created_at >= window_start,
                RiskAlert.created_at < as_of,
            )
        )
        or 0
    )

    supplier_total = session.scalar(select(func.count()).select_from(Supplier)) or 0
    active_supplier_total = (
        session.scalar(
            select(func.count()).select_from(Supplier).where(Supplier.enabled.is_(True))
        )
        or 0
    )

    source_rows = session.execute(
        select(
            DataSource.id,
            DataSource.code,
            DataSource.name,
            func.count(func.distinct(RiskAlert.id)),
        )
        .join(RawSignal, RawSignal.source_id == DataSource.id)
        .join(RiskEventSignal, RiskEventSignal.signal_id == RawSignal.id)
        .join(
            SupplierEventMatch,
            SupplierEventMatch.event_id == RiskEventSignal.event_id,
        )
        .join(RiskAlert, RiskAlert.match_id == SupplierEventMatch.id)
        .where(current_condition)
        .group_by(DataSource.id, DataSource.code, DataSource.name)
        .order_by(func.count(func.distinct(RiskAlert.id)).desc(), DataSource.code)
    ).all()
    source_distribution = [
        SourceDistributionItem(source_id=source_id, code=code, name=name, count=count)
        for source_id, code, name, count in source_rows
    ]

    recent_rows = session.execute(
        select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
        .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        .where(current_condition)
        .order_by(RiskAlert.updated_at.desc(), RiskAlert.id.desc())
        .limit(RECENT_ALERT_LIMIT)
    ).all()

    retention_window_days = get_retention_settings().event_days
    return DashboardAggregate(
        as_of=as_of,
        window_start=window_start,
        window_days=days,
        level_counts=level_counts,
        total_current=total_current,
        today_new=today_new,
        type_distribution=type_distribution,
        period_new_count=period_new_count,
        supplier_total=supplier_total,
        active_supplier_total=active_supplier_total,
        source_distribution=source_distribution,
        retention_window_days=retention_window_days,
        history_may_be_partial=days > retention_window_days,
        recent_rows=recent_rows,
    )


@dataclass
class _SupplierRegionAccumulator:
    """供应商省/地区聚合的中间值；同一供应商有多个站点时按供应商去重。"""

    name: str
    country_code: str | None
    region: str | None
    city: str | None
    supplier_ids: set[int]
    site_count: int
    coords: list[tuple[float, float]]
    levels: dict[str, int]
    alert_ids: set[int]


def _normalize_region(region: str | None) -> str | None:
    """把「浙江省 / 内蒙古自治区 / 台湾省」归一为省级简称，用于中心点查表。"""
    if region is None:
        return None
    value = region.strip()
    if not value:
        return None
    for suffix in (
        "特别行政区",
        "维吾尔自治区",
        "壮族自治区",
        "回族自治区",
        "自治区",
        "省",
        "市",
    ):
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _resolve_supplier_region_coords(
    *,
    country_code: str | None,
    region: str | None,
    city: str | None,
    site_coords: Sequence[tuple[float, float]],
) -> tuple[float, float] | None:
    """供应商省/地区点位坐标：省级中心点 > 城市中心点 > 站点均值 > 国家中心点。"""
    region_key = _normalize_region(region)
    if region_key is not None:
        centroid = PROVINCE_CENTROIDS.get(region_key)
        if centroid is not None:
            return (centroid[1], centroid[0])
    city_key = _normalize_region(city)
    if city_key is not None:
        city_centroid = CITY_CENTROIDS.get(city_key)
        if city_centroid is not None:
            return (city_centroid[1], city_centroid[0])
    if site_coords:
        return (
            sum(latitude for latitude, _longitude in site_coords) / len(site_coords),
            sum(longitude for _latitude, longitude in site_coords) / len(site_coords),
        )
    country_centroid = COUNTRY_CENTROIDS.get((country_code or "").upper())
    if country_centroid is None:
        return None
    return (country_centroid[1], country_centroid[0])


def _build_risk_points(
    session: Session,
    representatives: Sequence[RepresentativeRow],
) -> tuple[list[GeoRiskPoint], int]:
    """按供应商站点所在省/地区聚合地图点位。

    没有命中提醒但有启用供应商的省/地区也会返回（alert_count=0、max_level=None），
    前端用空心圆表示；命中提醒的点按最高等级填充。供应商有多个站点时，同一提醒
    会出现在多个省/地区，因此各点计数非互斥。
    """
    supplier_levels: dict[int, dict[str, int]] = {}
    supplier_alert_ids: dict[int, set[int]] = {}
    for alert, _match, _event, supplier in representatives:
        supplier_alert_ids.setdefault(supplier.id, set()).add(alert.id)
        levels = supplier_levels.setdefault(supplier.id, {})
        levels[alert.level] = levels.get(alert.level, 0) + 1

    site_rows = session.execute(
        select(
            SupplierSite.supplier_id,
            SupplierSite.country_code,
            SupplierSite.region,
            SupplierSite.city,
            SupplierSite.latitude,
            SupplierSite.longitude,
        )
        .join(Supplier, Supplier.id == SupplierSite.supplier_id)
        .where(Supplier.enabled.is_(True))
    ).all()

    points: dict[tuple[str, str], _SupplierRegionAccumulator] = {}
    for supplier_id, country_code, region, city, latitude, longitude in site_rows:
        region_key = _normalize_region(region) or _normalize_region(city)
        if region_key is None:
            continue
        country = (country_code or "").upper()
        key = (country, region_key)
        accumulator = points.get(key)
        if accumulator is None:
            accumulator = _SupplierRegionAccumulator(
                name=region or city or country or "未知地区",
                country_code=country_code,
                region=region,
                city=None if region is not None else city,
                supplier_ids=set(),
                site_count=0,
                coords=[],
                levels={level: 0 for level in CURRENT_LEVELS},
                alert_ids=set(),
            )
            points[key] = accumulator
        accumulator.supplier_ids.add(int(supplier_id))
        accumulator.site_count += 1
        if latitude is not None and longitude is not None:
            accumulator.coords.append((float(latitude), float(longitude)))

    risk_points: list[GeoRiskPoint] = []
    mapped_supplier_ids: set[int] = set()
    for item in points.values():
        coords = _resolve_supplier_region_coords(
            country_code=item.country_code,
            region=item.region,
            city=item.city,
            site_coords=item.coords,
        )
        if coords is None:
            continue
        for supplier_id in item.supplier_ids:
            item.alert_ids.update(supplier_alert_ids.get(supplier_id, set()))
            for level, count in supplier_levels.get(supplier_id, {}).items():
                item.levels[level] = item.levels.get(level, 0) + count
        mapped_supplier_ids.update(item.supplier_ids)
        risk_points.append(
            GeoRiskPoint(
                name=item.name,
                country_code=item.country_code,
                region=item.region,
                city=item.city,
                latitude=coords[0],
                longitude=coords[1],
                supplier_count=len(item.supplier_ids),
                site_count=item.site_count,
                alert_count=len(item.alert_ids),
                level_counts=[
                    LevelCount(level=level, count=item.levels.get(level, 0))
                    for level in CURRENT_LEVELS
                ],
                max_level=next(
                    (level for level in CURRENT_LEVELS if item.levels.get(level, 0) > 0),
                    None,
                ),
            )
        )

    # 排行口径：最高风险等级优先；同等级内再按该地区总风险数降序。
    risk_points.sort(
        key=lambda point: (
            LEVEL_ORDER.get(point.max_level or "", 9),
            -point.alert_count,
            -point.supplier_count,
            point.name,
        )
    )
    unlocated = sum(
        1
        for _alert, _match, _event, supplier in representatives
        if supplier.id not in mapped_supplier_ids
    )
    return risk_points, unlocated


def _build_supplier_points(session: Session) -> list[GeoSupplierPoint]:
    """供应商站点按城市聚合；只统计启用供应商且有完整坐标的站点。"""
    rows = session.execute(
        select(
            SupplierSite.city,
            SupplierSite.country_code,
            func.avg(SupplierSite.latitude),
            func.avg(SupplierSite.longitude),
            func.count(func.distinct(SupplierSite.supplier_id)),
            func.count(SupplierSite.id),
        )
        .join(Supplier, Supplier.id == SupplierSite.supplier_id)
        .where(Supplier.enabled.is_(True))
        .where(SupplierSite.latitude.is_not(None))
        .where(SupplierSite.longitude.is_not(None))
        .group_by(SupplierSite.city, SupplierSite.country_code)
    ).all()
    points = [
        GeoSupplierPoint(
            city=city or "未标注城市",
            country_code=country_code,
            latitude=float(latitude),
            longitude=float(longitude),
            supplier_count=int(supplier_count),
            site_count=int(site_count),
        )
        for city, country_code, latitude, longitude, supplier_count, site_count in rows
    ]
    points.sort(key=lambda point: (-point.supplier_count, point.city))
    return points


def _group_first_seen(group: Sequence[AlertRow]) -> datetime | None:
    """组首次入库时间：组内事件 ``created_at`` 的最早值。

    用事件入库时间而不是提醒行时间：规则版本切换会为同一批匹配新建提醒行，
    按提醒行计时会把老事件重复算进新日期。
    """
    earliest: datetime | None = None
    for _alert, _match, event, _supplier in group:
        created_at = event.created_at
        if not isinstance(created_at, datetime):
            continue
        if earliest is None or created_at < earliest:
            earliest = created_at
    return earliest


def _build_trend(
    *,
    representatives: Sequence[AlertRow],
    groups: Mapping[int, Sequence[AlertRow]],
    as_of: datetime,
    days: int,
) -> list[TrendPoint]:
    """按"组首次入库日"归集当前有效提醒，补零成连续 days 个点。

    与 KPI 共用同一读侧聚合：只统计 current_alert_condition 命中的代表行，
    每组只计一次；已失效行（含规则版本重算留下的 expired 副本）不进入趋势。
    """
    start = (as_of - timedelta(days=days - 1)).date()
    end = as_of.astimezone(UTC).date()
    counts: dict[date, int] = {}
    for alert, match, event, supplier in representatives:
        group = groups.get(alert.id) or ((alert, match, event, supplier),)
        first_seen = _group_first_seen(group)
        if first_seen is None:
            continue
        # 先显式转 UTC 再截日期：事件入库时间可能带其它时区偏移。
        day = first_seen.astimezone(UTC).date()
        if day < start or day > end:
            continue
        counts[day] = counts.get(day, 0) + 1
    return [
        TrendPoint(
            date=start + timedelta(days=offset),
            count=counts.get(start + timedelta(days=offset), 0),
        )
        for offset in range(days)
    ]


def build_dashboard_analytics(
    session: Session, *, days: int, now_utc: datetime | None = None
) -> DashboardAnalytics:
    """总览大屏的补充数据：按日趋势、供应商省/地区点位、供应商城市点位。

    与 build_dashboard_aggregate 共用同一 current_alert_condition 与代表行去重
    口径，保证地图/趋势与 KPI 不出现两套统计。本函数只读，不写库。
    """
    as_of = now_utc if now_utc is not None else utc_now()
    representatives, groups = representative_rows(
        session, current_alert_rows(session, now_utc=as_of)
    )
    risk_points, unlocated = _build_risk_points(session, representatives)
    return DashboardAnalytics(
        as_of=as_of,
        window_days=days,
        trend=_build_trend(
            representatives=representatives,
            groups=groups,
            as_of=as_of,
            days=days,
        ),
        risk_points=risk_points,
        supplier_points=_build_supplier_points(session),
        unlocated_alert_count=unlocated,
    )
