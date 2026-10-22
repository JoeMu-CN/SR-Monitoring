"""匹配柱：把风险事件与供应商清单按不同维度关联。

六个匹配柱独立成函数，维度模块按需声明启用哪些柱：
- entity    主体：注册号 / 法人全称 / 别名（强关联，复用现有确定性逻辑）
- location  地点：区县行政区名称精确匹配，不使用经纬度与影响半径；无法解析区县的事件地点
             仅在天气/地质事件下按省/市名称做弱匹配（site_region，低分）
- product   产品：受影响产品关键词 vs 供应产品
- country   国家：事件受影响国家（affected_countries）vs 供应商国别/生产地国别（宏观维度，弱关联）
- industry  行业：受影响行业 vs 供应商行业标签/关键原材料（宏观维度，单向包含）
- currency  币种：受影响币种（affected_currencies）vs 供应商结算币种（经济与金融维度，弱关联）

同一供应商被多根柱命中时按 MatchCandidate 合并（取最高分、并理由与证据）。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai.schemas import SignalAnalysisResult
from app.suppliers.models import Supplier, SupplierSite
from app.suppliers.schemas import normalize_alias

MATCH_ORDER = {
    "registry_no": 0,
    "legal_name": 1,
    "alias": 2,
    # site_distance 已不再由新匹配产生；保留仅为历史 SupplierEventMatch 排序兼容，
    # 避免既有行在 match_type() 重组时触发 KeyError。
    "site_distance": 3,
    "site_text": 4,
    "country": 5,
    "industry": 6,
    "product": 7,
    "currency": 8,
    # 省/市级弱匹配：仅天气与地质灾害事件、且事件地点解析不出区县时使用。
    "site_region": 9,
}

# 省/市弱匹配只对"以行政区范围为单位发布"的事件类型开放：中央气象台栏目公告
# 通常只给到省、市或区域，而供应商站点精确到区县。
_REGION_FALLBACK_EVENT_TYPES = frozenset({"weather", "geological"})
_REGION_SUFFIXES = ("特别行政区", "自治区", "省", "市")
_CITY_SUFFIXES = ("自治州", "地区", "市", "盟")
_MIN_REGION_CHARS = 2

_DISTRICT_SUFFIXES = ("自治县", "开发区", "新区", "区", "县", "旗")
_DISTRICT_TOKEN = re.compile(r"[^省市州盟县区旗]{1,16}(?:自治县|开发区|新区|区|县|旗)")


@dataclass
class MatchCandidate:
    supplier: Supplier
    match_types: set[str] = field(default_factory=set)
    association_score: int = 0
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, object]] = field(default_factory=list)

    def add(
        self,
        match_type: str,
        score: int,
        reason: str,
        evidence: dict[str, object],
    ) -> None:
        self.match_types.add(match_type)
        self.association_score = max(self.association_score, score)
        if reason not in self.reasons:
            self.reasons.append(reason)
        if evidence not in self.evidence:
            self.evidence.append(evidence)


def _candidate(
    matches: dict[int, MatchCandidate], supplier: Supplier
) -> MatchCandidate:
    return matches.setdefault(supplier.id, MatchCandidate(supplier=supplier))


def match_entities(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """主体柱：注册号 > 法人全称 > 别名，确定性精确匹配。"""
    for organization in result.organizations:
        normalized_name = normalize_alias(organization.name)
        for supplier in suppliers:
            if (
                organization.registry_no
                and supplier.registry_no
                and organization.registry_no == supplier.registry_no
            ):
                _candidate(matches, supplier).add(
                    "registry_no",
                    assoc.get("registry_no", 30),
                    f"注册编号精确匹配：{organization.registry_no}",
                    {
                        "object_type": "supplier",
                        "supplier_id": supplier.id,
                        "registry_no": organization.registry_no,
                    },
                )
            elif normalize_alias(supplier.legal_name) == normalized_name:
                _candidate(matches, supplier).add(
                    "legal_name",
                    assoc.get("legal_name", 25),
                    f"法人全称精确匹配：{organization.name}",
                    {
                        "object_type": "supplier",
                        "supplier_id": supplier.id,
                        "legal_name": supplier.legal_name,
                    },
                )
            else:
                alias = next(
                    (
                        item
                        for item in supplier.aliases
                        if item.normalized_alias == normalized_name
                    ),
                    None,
                )
                if alias is not None:
                    _candidate(matches, supplier).add(
                        "alias",
                        assoc.get("alias", 25),
                        f"供应商别名精确匹配：{alias.alias}",
                        {
                            "object_type": "alias",
                            "alias_id": alias.id,
                            "alias": alias.alias,
                        },
                    )


def _district_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_alias(value)
    for suffix in _DISTRICT_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _infer_district(*values: str | None) -> str | None:
    """从行政区名称、城市字段或地址中提取区县，兼容历史字段错位。"""
    for value in values:
        if not value:
            continue
        normalized = normalize_alias(value)
        matches = _DISTRICT_TOKEN.findall(normalized)
        if matches:
            return _district_key(matches[-1])
        direct = _district_key(normalized)
        if direct and any(normalized.endswith(suffix) for suffix in _DISTRICT_SUFFIXES):
            return direct
    return None


def _location_district(location: object) -> str | None:
    """优先取显式 district（可含县级市名），仅对 name/city 做保守后缀推断。"""
    explicit = getattr(location, "district", None)
    if explicit:
        return _district_key(explicit)
    return _infer_district(
        getattr(location, "name", None),
        getattr(location, "city", None),
    )


def _site_district(site: SupplierSite) -> str | None:
    """优先取显式 district（可含县级市名），仅对 city/名称/地址做保守后缀推断。"""
    if site.district:
        return _district_key(site.district)
    return _infer_district(site.city, site.site_name, site.address)


def matches_location_reference(location: object, site: SupplierSite) -> bool:
    """判断事件地点与生产地点是否满足区县级精确匹配。

    事件无法解析出区县时一律不匹配，不回退到省/市/地址文本包含。
    """
    location_district = _location_district(location)
    if not location_district:
        return False
    site_district = _site_district(site)
    if not site_district or location_district != site_district:
        return False
    for location_value, site_value in (
        (getattr(location, "country_code", None), site.country_code),
        (getattr(location, "region", None), site.region),
    ):
        if location_value and (
            not site_value or normalize_alias(site_value) != normalize_alias(location_value)
        ):
            return False
    location_city = getattr(location, "city", None)
    site_city = site.city
    # “上海市/宝山区”这类数据中，city 字段可能实际承载区县；
    # 区县已单独严格校验时不再用错位的 city 字段拒绝同一城市。
    if (
        location_city
        and site_city
        and _infer_district(location_city) is None
        and _infer_district(site_city) is None
        and normalize_alias(location_city) != normalize_alias(site_city)
    ):
        return False
    return True


def _region_key(value: str | None) -> str | None:
    """省级名称归一：去后缀（省/市/自治区/特别行政区），过短视为无效。"""
    if not value:
        return None
    normalized = normalize_alias(value)
    for suffix in _REGION_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized if len(normalized) >= _MIN_REGION_CHARS else None


def _city_key(value: str | None) -> str | None:
    """地级市名称归一：去后缀（市/地区/盟/自治州），过短视为无效。"""
    if not value:
        return None
    normalized = normalize_alias(value)
    for suffix in _CITY_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized if len(normalized) >= _MIN_REGION_CHARS else None


def region_match_precision(location: object, site: SupplierSite) -> str | None:
    """省市弱匹配：返回命中精度（``region`` / ``city``），不命中返回 ``None``。

    只用于事件地点解析不出区县的天气/地质事件（见 ``match_locations``）。规则刻意
    保守：国家必须一致；双方都给出省且不一致时直接不命中（避免同名城市跨省误配）；
    市名相等优先按市级命中，否则省名相等按省级命中。
    """
    event_country = getattr(location, "country_code", None)
    if event_country and site.country_code and event_country != site.country_code:
        return None
    event_region = _region_key(getattr(location, "region", None))
    site_region = _region_key(site.region)
    if event_region and site_region and event_region != site_region:
        return None
    event_city = _city_key(getattr(location, "city", None))
    site_city = _city_key(site.city)
    if event_city and site_city and event_city == site_city:
        return "city"
    if event_region and site_region and event_region == site_region:
        return "region"
    return None


def match_locations(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """地点柱：区县精确匹配（site_text）+ 天气/地质事件的省市弱匹配（site_region）。

    区县匹配口径不变；省市弱匹配只在事件类型为 weather/geological、且该地点
    解析不出区县时启用，避免区县级预警被放大成整个省/市的关联。
    """
    del session  # 保持 MatcherFn 签名兼容，地点柱不再访问数据库
    allow_region_fallback = result.event_type in _REGION_FALLBACK_EVENT_TYPES
    for location in result.locations:
        inferred_district = _location_district(location)
        if not inferred_district:
            if allow_region_fallback:
                _match_region_level(location, suppliers, assoc, matches)
            continue
        for supplier in suppliers:
            for site in supplier.sites:
                if location.country_code and site.country_code != location.country_code:
                    continue
                if not matches_location_reference(location, site):
                    continue
                _candidate(matches, supplier).add(
                    "site_text",
                    assoc.get("site_text", 20),
                    (
                        f"事件地点区级行政区精确匹配：{location.name}"
                        f"（{inferred_district}） → {site.site_name}"
                    ),
                    {
                        "object_type": "site",
                        "site_id": site.id,
                        "site_name": site.site_name,
                        "event_location": location.name,
                        "method": "text",
                        "district": inferred_district,
                        "precision": "district",
                    },
                )


def _match_region_level(
    location: object,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """按省/市名称做弱匹配，命中精度写入证据，分值低于区县精确匹配。"""
    for supplier in suppliers:
        for site in supplier.sites:
            precision = region_match_precision(location, site)
            if precision is None:
                continue
            level_label = "市级" if precision == "city" else "省级"
            _candidate(matches, supplier).add(
                "site_region",
                assoc.get("site_region", 10),
                (
                    f"事件行政区{level_label}匹配：{getattr(location, 'name', '')} → "
                    f"{site.site_name}（{site.region or site.city or '未知'}）"
                ),
                {
                    "object_type": "site",
                    "site_id": site.id,
                    "site_name": site.site_name,
                    "event_location": getattr(location, "name", None),
                    "method": "text",
                    "precision": "city" if precision == "city" else "region",
                },
            )


def _supplier_term_matches(term: str, affected: str) -> bool:
    """单个供应商侧词是否命中单个事件侧受影响词。

    精确相等始终命中；否则只接受"供应商词被事件词包含"这一单向包含，事件词必须是
    更具体的表述。禁止反向包含（事件泛化词是供应商词的前缀，例如事件"电梯"命中
    供应"电梯配件"、事件"机械"命中行业"机械设备制造"）——那会把无关供应商按产品
    或行业拉进匹配。空白词永不命中。产品柱与行业柱共用这条规则。
    """
    if not term or not affected:
        return False
    if term == affected:
        return True
    return len(term) >= 2 and term in affected


def match_products(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """产品柱：受影响产品关键词 vs 供应产品名称/关键词（单向包含，见 _supplier_term_matches）。"""
    del session  # 保持 MatcherFn 签名兼容，产品柱不访问数据库
    affected_products = [
        value for value in map(normalize_alias, result.affected_products) if value
    ]
    for supplier in suppliers:
        for product in supplier.products:
            terms = [normalize_alias(product.name), *map(normalize_alias, product.keywords)]
            matched_term = next(
                (
                    term
                    for term in terms
                    for affected in affected_products
                    if _supplier_term_matches(term, affected)
                ),
                None,
            )
            if matched_term is not None:
                _candidate(matches, supplier).add(
                    "product",
                    assoc.get("product", 12),
                    f"受影响产品关键词匹配：{matched_term} → {product.name}",
                    {
                        "object_type": "product",
                        "product_id": product.id,
                        "product_name": product.name,
                        "keyword": matched_term,
                    },
                )


def match_countries(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """国家柱（宏观维度）：事件的受影响国家范围 vs 供应商国别/生产地国别。

    来源只有 AI 判定的 ``affected_countries``，即"该国供应链整体受影响"的国家级
    系统性风险（全境制裁、战争、封锁、全国性动荡）。不再读 ``locations[].country_code``：
    事件发生地或公告涉及国几乎等于该国供应商的国别，会把同国供应商批量拉进匹配。

    弱关联，仅用于宏观事件的国别传导；单独命中不应触发高等级，由维度配置的强关联柱
    集合与强制规则约束。
    """
    country_codes = {code.strip().upper() for code in result.affected_countries if code.strip()}
    if not country_codes:
        return
    for supplier in suppliers:
        site_countries = {site.country_code for site in supplier.sites}
        hit = sorted(country_codes & ({supplier.country_code} | site_countries))
        for code in hit:
            _candidate(matches, supplier).add(
                "country",
                assoc.get("country", 8),
                f"事件受影响国家范围与供应商国别/生产地一致：{code}",
                {
                    "object_type": "country",
                    "supplier_id": supplier.id,
                    "country_code": code,
                },
            )


def match_industries(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """行业柱（宏观维度）：受影响行业 vs 供应商行业标签与关键原材料。

    匹配为单向包含（见 ``_supplier_term_matches``）：供应商词可以被更具体的
    事件行业词包含，但事件侧泛化词不得反吞供应商的宽口径行业标签。
    """
    affected = [normalize_alias(item) for item in result.affected_industries]
    if not affected:
        return
    for supplier in suppliers:
        industry_terms = [
            normalize_alias(value)
            for value in ([supplier.industry] if supplier.industry else [])
            if value
        ]
        industry_terms += [
            normalize_alias(value)
            for value in (supplier.raw_materials or [])
            if value
        ]
        matched = next(
            (
                term
                for term in industry_terms
                for item in affected
                if _supplier_term_matches(term, item)
            ),
            None,
        )
        if matched is not None:
            _candidate(matches, supplier).add(
                "industry",
                assoc.get("industry", 12),
                f"受影响行业/原材料匹配：{matched} → {supplier.legal_name}",
                {
                    "object_type": "industry",
                    "supplier_id": supplier.id,
                    "keyword": matched,
                },
            )


def match_currencies(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """币种柱（经济与金融维度）：事件受影响币种 vs 供应商结算币种。

    双侧 ``strip().upper()`` 归一后做精确集合相等：不做包含、不做模糊匹配，也不
    做国别→币种推导。供应商未填结算币种（None/空/纯空白）一律不命中（fail-closed），
    避免把同国但结算币种无关的供应商批量拉进汇率类事件匹配。

    弱关联：只表达汇兑与结算货币层面的成本传导。事件方向（升值/贬值）只决定评分
    阈值条件是否成立，不改变币种归属——本柱不看方向。
    """
    del session  # 保持 MatcherFn 签名兼容，币种柱不访问数据库
    affected = {code.strip().upper() for code in result.affected_currencies if code.strip()}
    if not affected:
        return
    for supplier in suppliers:
        settlement = (supplier.settlement_currency or "").strip().upper()
        if not settlement or settlement not in affected:
            continue
        _candidate(matches, supplier).add(
            "currency",
            assoc.get("currency", 15),
            f"结算币种匹配：{settlement} ↔ {supplier.legal_name}",
            {
                "object_type": "currency",
                "supplier_id": supplier.id,
                "currency": settlement,
            },
        )
