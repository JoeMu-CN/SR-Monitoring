"""情报聚合生成服务：政策/行业事件 → 简要报告 + 风险提醒建议。

分类归属规则（category）
------------------------
category 取**声明该事件信源的监控维度键**（policy / industry），与
``risks.engine.processing.resolve_dimension`` 的「信源优先」路由同源：
``DimensionConfig.data_sources`` 里声明了某个 ``source_code`` 的维度即拥有它。

与规则引擎的两处刻意差异，必须在测试中锁定：
1. 不看 ``enabled``。情报通道是宏观解读，不参与供应商评分与提醒生成；政策维度
   默认 enabled=False，若沿用引擎口径，政策情报将永远为空。
2. 不看 AI 事件类型。policy 维度声明 ``event_types=()``，按事件类型归属同样会漏。

一个事件的全部信号必须归属同一分类：跨分类（多个信源分别归 policy/industry）
视为归属冲突并跳过，不猜测。归属由静态维度声明推导，与 ``rule_dimension_configs``
的运行时启停/参数覆盖无关，因此不需要查库也不会随工作台配置漂移。

行情事件不走情报通道
--------------------
``market`` / ``exchange_rate`` 两类事件（``app.ai.schemas.MARKET_EVENT_TYPES``）即使
信源归属 policy / industry 也不会生成情报报告，由确定性行情链路（市场指标监控）单独
处理与呈现。该排除在 ``intelligence_batch._candidate_event_select`` 的 SQL 粗筛层
完成，避免行情点位被当作宏观文本解读并白白消耗模型额度；本模块仍保留直接调用
``generate_intelligence_report`` 的能力（人工补生成），不改变 policy / industry
文本事件既有的生成与幂等语义。

生成前的相关性门控（两段式）
----------------------------
``gate_event_for_intelligence`` 先判「这条公告是否值得进入情报页」，判定不相关时
以 ``status='skipped'`` 落库并**不发起报告调用**；判定失败落 ``failed``，进入既有
冷却重试。关注域由 ``load_intelligence_scope`` 组装：维度静态主题 + 启用供应商的
行业/原材料/产品关键词/国家。门控只拦截调度批量入口，人工补生成仍绕过门控。

幂等
----
同一 (event_id, category) 只有一行：已存在 ready 行的事件不再生成；failed 行在
``INTELLIGENCE_RETRY_COOLDOWN_HOURS`` 冷却期后允许重试；skipped 行视为已判定，
除非 ``gate_prompt_version`` 升级或运行重判脚本，否则不再重复调用门控。最终并发
兜底是 ``uq_intelligence_reports_event`` 唯一约束。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intelligence_providers import (
    IntelligenceProvider,
    build_intelligence_provider,
)
from app.ai.intelligence_schemas import (
    GATE_PROMPT_VERSION,
    INTELLIGENCE_CATEGORIES,
    INTELLIGENCE_PROMPT_VERSION,
    IntelligenceCategory,
    IntelligenceGateInput,
    IntelligenceInput,
    IntelligenceScope,
)
from app.ai.providers import AIProviderError
from app.config import (
    INTELLIGENCE_GATE_MAX_CONTENT_CHARS,
    INTELLIGENCE_GATE_SCOPE_KEYWORD_LIMIT,
    get_ai_settings,
)
from app.risks.engine.dimensions import default_dimensions
from app.risks.intelligence_models import IntelligenceReport
from app.risks.models import RiskEvent, RiskEventSignal
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier, SupplierProduct

logger = logging.getLogger("risks.intelligence")

#: 送给 LLM 的信源正文上限；超长公告截断，避免单次请求体积失控。
MAX_CONTENT_CHARS = 4000

#: 关注域中行业/国家列表的上限；超出部分对判定无增益，只抬提示词体积。
_SCOPE_LIST_LIMIT = 20

#: 供应商 industry 常见写法是用 # / , / ; 连接的多个词，拆开才能与事件交集。
_INDUSTRY_SPLIT = re.compile(r"[#,，;；/、]+")

#: 关注域关键词停用表（按 casefold 比较）：只描述品类大类的词几乎能「命中」任何工业
#: 公告，却没有区分度——例如「添加剂」会让饲料添加剂、食品添加剂授权被误判为与化学品
#: 业务相关，并在页面上留下噪声。只剔除这类通用词；具体品类（涂料、胶粘剂、清洗剂、
#: 紧固件等）保留，避免误伤召回。
GENERIC_SCOPE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"添加剂", "化工原料", "工业耗材"}
)


def _scope_keyword(value: str) -> str | None:
    """关注域关键词归一：去空白，通用品类词返回 None（不进入画像）。"""
    text = value.strip()
    if not text or text.casefold() in GENERIC_SCOPE_KEYWORDS:
        return None
    return text


def intelligence_source_categories() -> dict[str, IntelligenceCategory]:
    """信源 code → 情报分类；仅取 policy / industry 两个维度的静态声明。"""
    mapping: dict[str, IntelligenceCategory] = {}
    for dimension in default_dimensions():
        if dimension.key not in INTELLIGENCE_CATEGORIES:
            continue
        category: IntelligenceCategory = dimension.key
        for source in dimension.data_sources:
            mapping[source.code] = category
    return mapping


def resolve_intelligence_category(
    source_codes: frozenset[str] | set[str],
) -> IntelligenceCategory | None:
    """事件的信源集合 → 唯一情报分类；无归属或跨分类冲突都返回 None。"""
    mapping = intelligence_source_categories()
    categories = {mapping[code] for code in source_codes if code in mapping}
    return categories.pop() if len(categories) == 1 else None


def primary_signal_rows(session: Session, event_ids: list[int]) -> dict[int, RawSignal]:
    """每个事件取一条代表信号（发布时间最新、其次 id 最大），与提醒页口径一致。"""
    rows = session.execute(
        select(RiskEventSignal.event_id, RawSignal)
        .join(RawSignal, RiskEventSignal.signal_id == RawSignal.id)
        .where(RiskEventSignal.event_id.in_(event_ids))
        .order_by(
            RiskEventSignal.event_id,
            RawSignal.published_at.desc().nullslast(),
            RawSignal.id.desc(),
        )
    ).all()
    signals: dict[int, RawSignal] = {}
    for event_id, signal in rows:
        signals.setdefault(event_id, signal)
    return signals


def build_intelligence_input(
    session: Session, event: RiskEvent, category: IntelligenceCategory
) -> IntelligenceInput | None:
    """组装 LLM 输入；事件已无可用信号（历史数据被清理）时返回 None。"""
    signals = primary_signal_rows(session, [event.id])
    signal = signals.get(event.id)
    if signal is None:
        return None
    source = session.get(DataSource, signal.source_id)
    if source is None:
        return None
    return IntelligenceInput(
        event_id=event.id,
        category=category,
        source_code=source.code,
        source_name=source.name,
        title=signal.title,
        summary=event.summary,
        content=signal.content[:MAX_CONTENT_CHARS],
        url=signal.url,
        published_at=signal.published_at,
    )


def load_intelligence_scope(
    session: Session, category: IntelligenceCategory
) -> IntelligenceScope:
    """组装门控关注域：维度静态主题 + 启用供应商的行业/原材料/产品关键词/国家。

    通用品类词（``GENERIC_SCOPE_KEYWORDS``：添加剂/化工原料/工业耗材）先被剔除再
    参与截断，避免「饲料添加剂授权」这类公告因字面命中而留在情报页。

    画像可能很稀疏（当前多数供应商 ``industry`` 为空），这里只做「有什么给什么」：
    空集合的语义由门控提示词兜底（拿不准一律放行），不得据此判定不相关。
    """
    themes = [
        item
        for dimension in default_dimensions()
        if dimension.key == category
        for item in dimension.content_items
    ]
    suppliers = list(
        session.scalars(select(Supplier).where(Supplier.enabled.is_(True)))
    )
    industries: set[str] = set()
    countries: set[str] = set()
    keyword_counts: Counter[str] = Counter()
    for supplier in suppliers:
        if supplier.country_code:
            countries.add(supplier.country_code)
        if supplier.industry:
            for token in _INDUSTRY_SPLIT.split(supplier.industry):
                text = token.strip()
                if text:
                    industries.add(text)
        for material in supplier.raw_materials or []:
            if not isinstance(material, str):
                continue
            keyword = _scope_keyword(material)
            if keyword is not None:
                keyword_counts[keyword] += 1
    supplier_ids = [supplier.id for supplier in suppliers]
    if supplier_ids:
        for product in session.scalars(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id.in_(supplier_ids)
            )
        ):
            for keyword in product.keywords or []:
                if not isinstance(keyword, str):
                    continue
                normalized = _scope_keyword(keyword)
                if normalized is not None:
                    keyword_counts[normalized] += 1
    # 按出现频次取前 N，再按名称稳定排序：同样的画像永远生成同样的提示词。
    ranked = sorted(
        keyword_counts.items(), key=lambda item: (-item[1], item[0])
    )[:INTELLIGENCE_GATE_SCOPE_KEYWORD_LIMIT]
    return IntelligenceScope(
        themes=themes,
        industries=sorted(industries)[:_SCOPE_LIST_LIMIT],
        product_keywords=sorted(keyword for keyword, _ in ranked),
        countries=sorted(countries)[:_SCOPE_LIST_LIMIT],
        supplier_count=len(suppliers),
    )


def _facts_list(event: RiskEvent, key: str) -> list[str]:
    """读取事件 facts 中的字符串数组（AI 解析快照；历史事件可能是空对象）。"""
    facts = event.facts if isinstance(event.facts, dict) else {}
    raw = facts.get(key)
    if not isinstance(raw, list):
        return []
    values = [str(item).strip() for item in raw if str(item).strip()]
    return values[:8]


def build_gate_input(
    session: Session,
    event: RiskEvent,
    category: IntelligenceCategory,
    scope: IntelligenceScope,
) -> IntelligenceGateInput | None:
    """组装门控输入；与报告输入同源，但正文只取前 N 字、额外附上关注域。"""
    signals = primary_signal_rows(session, [event.id])
    signal = signals.get(event.id)
    if signal is None:
        return None
    source = session.get(DataSource, signal.source_id)
    if source is None:
        return None
    return IntelligenceGateInput(
        event_id=event.id,
        category=category,
        source_code=source.code,
        source_name=source.name,
        title=signal.title,
        summary=event.summary,
        content=signal.content[:INTELLIGENCE_GATE_MAX_CONTENT_CHARS],
        event_type=event.event_type,
        event_subtype=event.event_subtype,
        affected_products=_facts_list(event, "affected_products"),
        affected_industries=_facts_list(event, "affected_industries"),
        affected_countries=_facts_list(event, "affected_countries"),
        published_at=signal.published_at,
        scope=scope,
    )


def _report_row(
    session: Session,
    event: RiskEvent,
    category: IntelligenceCategory,
    *,
    provider_name: str,
    model: str,
) -> IntelligenceReport:
    """取或建唯一 (event_id, category) 行；provider/model 只在新建时写入。"""
    report = session.scalar(
        select(IntelligenceReport).where(
            IntelligenceReport.event_id == event.id,
            IntelligenceReport.category == category,
        )
    )
    if report is None:
        report = IntelligenceReport(
            event_id=event.id,
            category=category,
            provider=provider_name,
            model=model,
            prompt_version=INTELLIGENCE_PROMPT_VERSION,
            status="failed",
            suggestions=[],
        )
        session.add(report)
    return report


async def gate_event_for_intelligence(
    session: Session,
    event: RiskEvent,
    category: IntelligenceCategory,
    *,
    scope: IntelligenceScope,
    provider: IntelligenceProvider | None = None,
) -> IntelligenceReport | None:
    """生成前的相关性门控：返回 None 表示放行，否则返回已落库的 skipped/failed 行。

    放行时不写行——报告成功由 ``generate_intelligence_report`` 落 ready、失败落
    failed，状态语义不与门控混淆。判定不相关写 ``skipped`` + ``gate_reason``；
    门控调用失败写 ``failed`` + error，进入既有冷却重试而不阻塞整批。
    """
    payload = build_gate_input(session, event, category, scope)
    if payload is None:
        return None
    active = provider or build_intelligence_provider(get_ai_settings())

    try:
        result = await active.gate_intelligence(payload)
    except AIProviderError as exc:
        report = _report_row(
            session,
            event,
            category,
            provider_name=active.provider_name,
            model=active.model,
        )
        report.status = "failed"
        report.error = f"门控失败：{exc}"[:2000]
        report.gate_reason = None
        report.gate_prompt_version = GATE_PROMPT_VERSION
        report.report_zh = None
        report.suggestions = []
        report.generated_at = None
        session.commit()
        logger.warning("情报门控失败: event=%s category=%s: %s", event.id, category, exc)
        return report

    if result.relevant:
        return None

    report = _report_row(
        session,
        event,
        category,
        provider_name=active.provider_name,
        model=active.model,
    )
    report.status = "skipped"
    report.gate_reason = result.reason_zh
    report.gate_prompt_version = GATE_PROMPT_VERSION
    report.report_zh = None
    report.suggestions = []
    report.error = None
    report.generated_at = None
    session.commit()
    logger.info(
        "情报门控跳过: event=%s category=%s: %s", event.id, category, result.reason_zh
    )
    return report


async def generate_intelligence_report(
    session: Session,
    event: RiskEvent,
    category: IntelligenceCategory,
    *,
    provider: IntelligenceProvider | None = None,
    gated: bool = False,
) -> IntelligenceReport | None:
    """生成并落库一份情报报告（成功 ready / 失败 failed+error）。

    事件无可用信号时返回 None（不写 failed 行：这不是模型故障，只是没有可解读
    的公开文本，重试也不会改变）。人工补生成走这里，不经门控（``gated=False``）。
    ``gated=True`` 只用于标记「本次生成前已通过门控」，便于区分口径与重判范围。
    """
    payload = build_intelligence_input(session, event, category)
    if payload is None:
        return None
    active = provider or build_intelligence_provider(get_ai_settings())
    report = _report_row(
        session,
        event,
        category,
        provider_name=active.provider_name,
        model=active.model,
    )

    try:
        result = await active.generate_intelligence(payload)
    except AIProviderError as exc:
        report.status = "failed"
        report.error = str(exc)[:2000]
        report.report_zh = None
        report.suggestions = []
        report.generated_at = None
        session.commit()
        logger.warning("情报报告生成失败: event=%s category=%s: %s", event.id, category, exc)
        return report

    report.status = "ready"
    report.report_zh = result.report_zh
    report.suggestions = list(result.suggestions)
    report.error = None
    report.gate_reason = None
    if gated:
        report.gate_prompt_version = GATE_PROMPT_VERSION
    report.generated_at = datetime.now(UTC)
    session.commit()
    session.refresh(report)
    return report
