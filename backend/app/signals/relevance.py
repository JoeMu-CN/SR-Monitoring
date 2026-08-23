"""LLM 解析前的确定性相关性预过滤。

目标：在调用 LLM 解析前，用确定性规则过滤掉"明确不相关"的信号，节省 token。
保守原则：拿不准一律放行（宁可多解析，不可漏风险）。

判定优先级：
1. 命中供应商主体/别名 → 放行（最强相关信号）
2. 命中中国地名且存在中国供应商 → 放行
3. 命中国外地理（国家全名/美国州）且这些国家均无供应商 → 过滤
   （例外：海外供应链重点关注国家（PRIORITY_COUNTRIES，默认日韩）即使
     暂无该国供应商也放行，避免误滤海外供应链风险信号）
4. 其余（无地理线索、拿不准）→ 放行

注意：raw_data 是标准化信号（不含原始 item 的经纬度），故本轮只做文本地理粗筛；
坐标地理围栏留待 adapter 保留原始坐标后作为增强。
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PRIORITY_COUNTRIES
from app.suppliers.models import Supplier
from app.suppliers.schemas import normalize_alias

# 美国州缩写：匹配 "..., XX" 边界（USGS 地名格式），在保留大小写的原文上找
_US_ABBR_PATTERN = re.compile(r",\s*([A-Z]{2})(?=[\s,.]|$)")

# 中国地名（省级 + 直辖市 + 自治区 + 统称）。命中即认为可能是国内事件。
_CHINA_KEYWORDS = frozenset(
    {
        "中国", "国内", "北京", "上海", "天津", "重庆",
        "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽",
        "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
        "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
        "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
    }
)

# 国外国家/地区全名（小写）→ ISO 3166-1 alpha-2。只用全名，不用两字母码避免歧义。
_COUNTRY_KEYWORDS: dict[str, str] = {
    "美国": "US", "united states": "US", "usa": "US", "america": "US",
    "日本": "JP", "japan": "JP",
    "韩国": "KR", "south korea": "KR", "korea": "KR",
    "德国": "DE", "germany": "DE",
    "法国": "FR", "france": "FR",
    "英国": "GB", "united kingdom": "GB", "britain": "GB",
    "意大利": "IT", "italy": "IT",
    "西班牙": "ES", "spain": "ES",
    "荷兰": "NL", "netherlands": "NL",
    "比利时": "BE", "belgium": "BE",
    "瑞士": "CH", "switzerland": "CH",
    "瑞典": "SE", "sweden": "SE",
    "挪威": "NO", "norway": "NO",
    "波兰": "PL", "poland": "PL",
    "奥地利": "AT", "austria": "AT",
    "捷克": "CZ", "czech": "CZ",
    "匈牙利": "HU", "hungary": "HU",
    "希腊": "GR", "greece": "GR",
    "葡萄牙": "PT", "portugal": "PT",
    "印度": "IN", "india": "IN",
    "越南": "VN", "vietnam": "VN",
    "泰国": "TH", "thailand": "TH",
    "印度尼西亚": "ID", "印尼": "ID", "indonesia": "ID",
    "马来西亚": "MY", "malaysia": "MY",
    "新加坡": "SG", "singapore": "SG",
    "菲律宾": "PH", "philippines": "PH",
    "澳大利亚": "AU", "澳洲": "AU", "australia": "AU",
    "新西兰": "NZ", "new zealand": "NZ",
    "加拿大": "CA", "canada": "CA",
    "墨西哥": "MX", "mexico": "MX",
    "巴西": "BR", "brazil": "BR",
    "阿根廷": "AR", "argentina": "AR",
    "智利": "CL", "chile": "CL",
    "秘鲁": "PE", "peru": "PE",
    "俄罗斯": "RU", "russia": "RU",
    "土耳其": "TR", "turkey": "TR",
    "沙特": "SA", "saudi": "SA",
    "阿联酋": "AE", "uae": "AE",
    "南非": "ZA", "south africa": "ZA",
    "埃及": "EG", "egypt": "EG",
    "巴基斯坦": "PK", "pakistan": "PK",
    "孟加拉": "BD", "bangladesh": "BD",
    "伊朗": "IR", "iran": "IR",
    "以色列": "IL", "israel": "IL",
}

# 美国州全名（小写）→ US。USGS 等英文源常用地名。
_US_STATE_NAMES = frozenset(
    {
        "california", "texas", "florida", "alaska", "hawaii", "nevada",
        "oklahoma", "washington", "oregon", "utah", "colorado", "idaho",
        "montana", "wyoming", "arizona", "new mexico", "kansas", "arkansas",
        "missouri", "illinois", "ohio", "georgia", "virginia", "new york",
    }
)

# 美国州两字母缩写：仅在 "..., XX" 结尾模式匹配（USGS 地名格式），避免歧义。
_US_STATE_ABBR = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
        "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
        "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
        "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
        "VT", "VA", "WA", "WV", "WI", "WY",
    }
)

# 高影响主题不因地理字段不完整而预过滤，交给模型和本地规则继续判断。
# 关键词全小写（normalize_alias 已 casefold）；命中即强制放行复核。
_HIGH_IMPACT_KEYWORDS = frozenset(
    {
        # 制裁与出口管制
        "制裁", "sanction", "出口管制", "export control", "实体清单", "entity list",
        "不可靠实体", "出口管制实体",
        # 供应链合规（EU CBAM/CSDDD、美国 UFLPA 涉疆）
        "carbon border", "cbam", "due diligence", "尽职调查",
        "forced labour", "forced labor", "强迫劳动", "uflpa", "涉疆",
        # 供应中断
        "供应中断", "供应链中断", "supply disruption", "supply chain disruption",
        # 宏观政策（利率/PMI 等融资成本与需求指标）
        "lpr", "贷款市场报价", "利率调整", "采购经理指数", "采购经理", "pmi",
        "货币政策",
        # 行业监管（环保/生产安全：环评、督察、排污、黑名单等）
        "环评", "环境影响评价", "督察", "排污", "黑名单", "突发环境事件",
        "专项整治", "涉刑",
        # 生产中断
        "停产", "停工", "重大事故",
        # 重大灾害
        "重大灾害", "台风", "地震", "洪水", "海啸", "火山",
        # 司法
        "司法", "失信被执行", "刑事立案",
    }
)


# 全量实体清单类信源：每次采集返回全量清单（指纹去重后不重复入库），
# 分析链无需对每条实体调用 LLM——仅当实体命中供应商名时才进入分析。
# 默认集合（可通过 signal-filter 配置覆盖）。
_LIST_SOURCE_CODES = frozenset(
    {
        "ofac-sdn",
        "bis-entity-list",
        "uflpa-entity-list",
        "mofcom-entity-detail",
        "mofcom-entity-control",
    }
)


@dataclass(frozen=True)
class FilterRules:
    """信号过滤规则（DB 配置合并代码默认值）。"""

    high_impact: frozenset[str]
    priority_countries: frozenset[str]
    list_sources: frozenset[str]
    commodity_threshold_pct: float = 5.0


FILTER_CONFIG_KEY = "signal-filter"
_RULES_TTL_SECONDS = 60.0
_rules_cache_until: float = 0.0
_rules_cache: FilterRules | None = None


def _default_rules() -> FilterRules:
    return FilterRules(
        high_impact=_HIGH_IMPACT_KEYWORDS,
        priority_countries=PRIORITY_COUNTRIES,
        list_sources=_LIST_SOURCE_CODES,
        commodity_threshold_pct=5.0,
    )


def load_filter_rules(session: Session) -> FilterRules:
    """读取信号过滤规则（DB 覆盖 + 代码默认兜底），TTL 60 秒缓存热更新。

    配置存储于 rule_dimension_configs（key=signal-filter）JSONB：
    {"high_impact": [...], "priority_countries": [...], "list_sources": [...]}。
    未配置的键沿用代码默认值；删除配置行即回退默认。
    """
    import time

    global _rules_cache_until, _rules_cache
    now = time.monotonic()
    if _rules_cache is not None and now < _rules_cache_until:
        return _rules_cache
    defaults = _default_rules()
    rules = defaults
    try:
        from app.risks.models import RuleDimensionConfig

        row = session.scalar(
            select(RuleDimensionConfig).where(RuleDimensionConfig.key == FILTER_CONFIG_KEY)
        )
        if row is not None and isinstance(row.config, dict) and row.enabled:
            cfg = row.config

            def _frozenset_of(
                value: object,
                fallback: frozenset[str],
                *,
                upper: bool = False,
            ) -> frozenset[str]:
                """提取配置列表为 frozenset；空/非法回退默认。upper 用于 ISO 国家码。"""
                if isinstance(value, (list, tuple)):
                    cleaned = frozenset(
                        str(k).strip().upper() if upper else str(k).strip()
                        for k in value
                        if isinstance(k, str) and k.strip()
                    )
                    if cleaned:
                        return cleaned
                return fallback

            def _threshold_float(value: object, fallback: float) -> float:
                """提取非负浮点阈值；非法/负数回退默认。"""
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if value >= 0:
                        return float(value)
                if isinstance(value, str):
                    try:
                        parsed = float(value.strip())
                    except ValueError:
                        return fallback
                    if parsed >= 0:
                        return parsed
                return fallback

            rules = FilterRules(
                high_impact=_frozenset_of(
                    cfg.get("high_impact"), defaults.high_impact
                ),
                priority_countries=_frozenset_of(
                    cfg.get("priority_countries"),
                    defaults.priority_countries,
                    upper=True,
                ),
                list_sources=_frozenset_of(
                    cfg.get("list_sources"), defaults.list_sources
                ),
                commodity_threshold_pct=_threshold_float(
                    cfg.get("commodity_threshold_pct"),
                    defaults.commodity_threshold_pct,
                ),
            )
    except Exception:  # noqa: BLE001 —— 配置读取失败回退默认（保守安全侧）
        rules = defaults
    _rules_cache = rules
    _rules_cache_until = now + _RULES_TTL_SECONDS
    return rules


def invalidate_filter_rules_cache() -> None:
    """配置更新后立即失效缓存（下次读取重载）。"""
    global _rules_cache_until, _rules_cache
    _rules_cache = None
    _rules_cache_until = 0.0


@dataclass(frozen=True)
class RelevanceDecision:
    relevant: bool
    reason: str


def assess_signal_relevance(
    session: Session, title: str, content: str
) -> RelevanceDecision:
    """对原始信号文本做确定性相关性判定。relevant=False 表示明确不相关可跳过 LLM。"""
    text = normalize_alias(f"{title} {content}")
    rules = load_filter_rules(session)
    if any(keyword in text for keyword in rules.high_impact):
        return RelevanceDecision(True, "命中高影响主题，强制放行复核")
    suppliers = list(
        session.scalars(select(Supplier).where(Supplier.enabled.is_(True)))
    )

    # 1. 供应商主体/别名命中 → 强相关
    for supplier in suppliers:
        if normalize_alias(supplier.legal_name) in text:
            return RelevanceDecision(True, f"命中供应商主体：{supplier.legal_name}")
        for alias in supplier.aliases:
            if alias.normalized_alias and alias.normalized_alias in text:
                return RelevanceDecision(True, f"命中供应商别名：{alias.alias}")

    supplier_countries = {supplier.country_code for supplier in suppliers}

    # 2. 中国地名 + 中国供应商 → 相关
    if any(keyword in text for keyword in _CHINA_KEYWORDS):
        if "CN" in supplier_countries:
            return RelevanceDecision(True, "命中中国地理且存在中国供应商")
        # 中国事件但无中国供应商：不直接过滤，交给后续国外判断/保守放行

    # 3. 国外地理命中，且命中国家均无供应商 → 明确不相关
    #    （海外供应链重点关注国家例外：即使暂无该国供应商也放行）
    foreign = _foreign_countries(text, f"{title} {content}")
    if foreign and not (foreign & supplier_countries):
        priority_hit = foreign & rules.priority_countries
        if priority_hit:
            return RelevanceDecision(
                True,
                f"命中重点关注国家 {sorted(priority_hit)}，海外供应链风险放行",
            )
        return RelevanceDecision(
            False, f"事件地理 {sorted(foreign)} 均无供应商，明确不相关"
        )

    # 4. 保守放行
    return RelevanceDecision(True, "未命中明确无关特征")


def _foreign_countries(normalized_text: str, raw_text: str) -> set[str]:
    """从文本提取国外国家代码集合（全名小写匹配 + 美州名 + 美州缩写结尾模式）。"""
    found: set[str] = set()
    for keyword, code in _COUNTRY_KEYWORDS.items():
        if keyword in normalized_text:
            found.add(code)
    for state in _US_STATE_NAMES:
        if state in normalized_text:
            found.add("US")
    # 美国州缩写：匹配 "..., XX" 边界（USGS 地名格式），在保留大小写的原文上找
    for match in _US_ABBR_PATTERN.finditer(raw_text):
        if match.group(1) in _US_STATE_ABBR:
            found.add("US")
            break
    return found


# 结构化宏观信号分级：commodity-futures 等价格信号按涨跌幅阈值免 LLM。
# 低于阈值（正常波动，无风险信息量）→ 跳过；达到阈值 → 进分析链。
_PCT_RE = re.compile(r"较昨结算：\s*([+-]?\d+(?:\.\d+)?)\s*%")
_COMMODITY_SOURCE = "commodity-futures"


def grade_structured_signal(
    source_code: str, title: str, content: str, rules: FilterRules
) -> str | None:
    """对结构化宏观信号做确定性分级。

    返回 None=放行进入分析链；返回 reason=跳过（写 filtered 记录，不耗 LLM）。
    当前仅 commodity-futures 按涨跌幅阈值分级；其余宏观信源频率低，保持全分析。
    """
    if source_code != _COMMODITY_SOURCE:
        return None
    m = _PCT_RE.search(f"{title} {content}")
    if not m:
        return None  # 解析不到涨跌幅 → 保守放行
    try:
        pct = abs(float(m.group(1)))
    except ValueError:
        return None
    threshold = rules.commodity_threshold_pct
    if pct < threshold:
        return (
            f"大宗商品涨跌幅 {m.group(1)}% 低于阈值 {threshold:g}%，"
            "正常波动免 LLM 分析"
        )
    return None
