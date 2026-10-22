"""风险提醒的读侧聚合：同供应商 + 同区域 + 时间重叠 + 同事件类型合并展示。

背景：中央气象台公告类信源一次台风过程会连发多篇公告（每日天气提示、重要天气
提示、台风预警），每篇生成独立事件并各自匹配同一批供应商；等级与时效都正确，但
同一供应商会在提醒列表里重复出现。本模块只做"读侧分组"，不改写库表：

- 事件、匹配、提醒全部保留，分组只决定列表/详情里哪些提醒合并成一条展示；
- 区域按事件地点最细一级取键（区县 → 市 → 省），带层级前缀，避免"天津市河北区"
  与"河北省"这类同名跨省误并；
- 时间取事件区间 ``[start_at, end_at]``，缺失时回落到 ``created_at`` / ``valid_until``；
- 事件类型必须相同（weather 只与 weather 合并），允许跨信源；
- 任一地点解析不出区域键，或时间区间缺失到无法判断时，保守地不参与合并。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.suppliers.schemas import normalize_alias


@dataclass(frozen=True, slots=True)
class AlertGroupMember:
    """参与分组的最小事实集（不依赖 ORM，便于纯函数测试）。"""

    alert_id: int
    supplier_id: int
    event_type: str
    event_id: int
    level: str
    score: int
    updated_at: datetime
    start_at: datetime
    end_at: datetime
    region_keys: frozenset[str]

    @property
    def bucket(self) -> tuple[int, str]:
        """只有同供应商、同事件类型的提醒才可能合并。"""
        return (self.supplier_id, self.event_type)


def location_region_key(
    *, region: str | None, city: str | None, district: str | None
) -> str | None:
    """把单条事件地点归一到最细一级的区域键。

    层级前缀是必需的：省级 ``region:河北`` 与区县级 ``district:天津::河北``
    语义完全不同，去掉前缀会误并。
    """
    normalized_region = normalize_alias(region) if region else ""
    normalized_city = normalize_alias(city) if city else ""
    normalized_district = normalize_alias(district) if district else ""
    if normalized_district:
        return f"district:{normalized_region}:{normalized_city}:{normalized_district}"
    if normalized_city:
        return f"city:{normalized_region}:{normalized_city}"
    if normalized_region:
        return f"region:{normalized_region}"
    return None


def event_region_keys(locations: Iterable[object]) -> frozenset[str]:
    """事件全部地点的区域键集合；解析不出任何键时返回空集（不参与合并）。"""
    keys: set[str] = set()
    for location in locations:
        key = location_region_key(
            region=getattr(location, "region", None),
            city=getattr(location, "city", None),
            district=getattr(location, "district", None),
        )
        if key is not None:
            keys.add(key)
    return frozenset(keys)


def event_time_window(
    *,
    start_at: datetime | None,
    end_at: datetime | None,
    valid_until: datetime | None,
    created_at: datetime | None,
    updated_at: datetime | None,
) -> tuple[datetime, datetime] | None:
    """事件的有效展示区间；两端都无法确定时返回 ``None``（不参与合并）。

    优先用 AI 解析的事件时间，其次用事件有效期，最后回落到记录时间；结束时间早于
    开始时间时收敛为同一时刻，避免产生负区间。
    """
    start = start_at or created_at
    end = end_at or valid_until or updated_at or start
    if start is None:
        return None
    if start.tzinfo is None or (end is not None and end.tzinfo is None):
        return None
    if end is None:
        return None
    if end < start:
        end = start
    return start, end


def _windows_overlap(
    left: tuple[datetime, datetime], right: tuple[datetime, datetime]
) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def should_merge(left: AlertGroupMember, right: AlertGroupMember) -> bool:
    """两条提醒是否属于同一展示组：同桶 + 时间重叠 + 区域有交集。"""
    if left.bucket != right.bucket:
        return False
    if not left.region_keys or not right.region_keys:
        return False
    if not (left.region_keys & right.region_keys):
        return False
    return _windows_overlap(
        (left.start_at, left.end_at), (right.start_at, right.end_at)
    )


def _find(parents: list[int], index: int) -> int:
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def group_alert_members(
    members: Sequence[AlertGroupMember],
) -> list[list[AlertGroupMember]]:
    """并查集分组，保持输入顺序稳定；同供应商同类型分桶后再两两比较。

    规模受"当前提醒"数量约束（通常几十到几百条），O(n²) 足够；分桶后实际比较量
    远小于全量。
    """
    buckets: dict[tuple[int, str], list[AlertGroupMember]] = {}
    for member in members:
        buckets.setdefault(member.bucket, []).append(member)

    groups: list[list[AlertGroupMember]] = []
    for bucket in buckets.values():
        parents = list(range(len(bucket)))
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                if should_merge(bucket[i], bucket[j]):
                    root_i, root_j = _find(parents, i), _find(parents, j)
                    if root_i != root_j:
                        parents[root_j] = root_i

        collected: dict[int, list[AlertGroupMember]] = {}
        for index, member in enumerate(bucket):
            collected.setdefault(_find(parents, index), []).append(member)
        groups.extend(collected.values())
    return groups


_LEVEL_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def representative(group: Sequence[AlertGroupMember]) -> AlertGroupMember:
    """组代表：等级最高（P1 最优先），同级取最近更新的一条。"""
    return min(
        group,
        key=lambda member: (
            _LEVEL_ORDER.get(member.level, len(_LEVEL_ORDER)),
            -member.updated_at.timestamp(),
        ),
    )
