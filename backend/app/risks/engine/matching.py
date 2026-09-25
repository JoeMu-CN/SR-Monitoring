"""匹配柱：把风险事件与供应商清单按不同维度关联。

五个匹配柱独立成函数，维度模块按需声明启用哪些柱：
- entity    主体：注册号 / 法人全称 / 别名（强关联，复用现有确定性逻辑）
- location  地点：区县行政区名称精确匹配，不使用经纬度与影响半径；无法解析区县的事件地点不匹配
- product   产品：受影响产品关键词 vs 供应产品
- country   国家：事件国家 vs 供应商国别/生产地国别（宏观维度，弱关联）
- industry  行业：受影响产品 vs 供应商行业标签/关键原材料（宏观维度）

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
}

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


def match_locations(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """地点柱：仅区县行政区名称精确匹配；无法解析区县的事件地点不参与匹配。"""
    del session  # 保持 MatcherFn 签名兼容，地点柱不再访问数据库
    for location in result.locations:
        inferred_district = _location_district(location)
        if not inferred_district:
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


def match_products(
    session: Session,
    result: SignalAnalysisResult,
    suppliers: Iterable[Supplier],
    assoc: dict[str, int],
    matches: dict[int, MatchCandidate],
) -> None:
    """产品柱：受影响产品关键词 vs 供应产品名称/关键词（双向包含）。"""
    affected_products = [normalize_alias(item) for item in result.affected_products]
    for supplier in suppliers:
        for product in supplier.products:
            terms = [normalize_alias(product.name), *map(normalize_alias, product.keywords)]
            matched_term = next(
                (
                    term
                    for term in terms
                    for affected in affected_products
                    if len(term) >= 2 and (term in affected or affected in term)
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
    """国家柱（宏观维度）：事件国家 vs 供应商国别/生产地国别。

    弱关联，仅用于宏观事件（制裁/关税/战争）的国别传导；
    单独命中不应触发高等级，由维度配置的强关联柱集合与强制规则约束。
    """
    country_codes = {
        loc.country_code for loc in result.locations if loc.country_code
    }
    if not country_codes:
        return
    for supplier in suppliers:
        site_countries = {site.country_code for site in supplier.sites}
        hit = sorted(country_codes & ({supplier.country_code} | site_countries))
        for code in hit:
            _candidate(matches, supplier).add(
                "country",
                assoc.get("country", 8),
                f"事件涉及国家与供应商国别/生产地一致：{code}",
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
    """行业柱（宏观维度）：受影响产品/行业 vs 供应商行业标签与关键原材料。"""
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
                if len(term) >= 2 and (term in item or item in term)
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
