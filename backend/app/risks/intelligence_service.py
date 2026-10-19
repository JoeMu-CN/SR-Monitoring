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

幂等
----
同一 (event_id, category) 只有一行：已存在 ready 行的事件不再生成；failed 行在
``INTELLIGENCE_RETRY_COOLDOWN_HOURS`` 冷却期后允许重试。最终并发兜底是
``uq_intelligence_reports_event`` 唯一约束。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.intelligence_providers import (
    IntelligenceProvider,
    build_intelligence_provider,
)
from app.ai.intelligence_schemas import (
    INTELLIGENCE_CATEGORIES,
    INTELLIGENCE_PROMPT_VERSION,
    IntelligenceCategory,
    IntelligenceInput,
)
from app.ai.providers import AIProviderError
from app.config import get_ai_settings
from app.risks.engine.dimensions import default_dimensions
from app.risks.intelligence_models import IntelligenceReport
from app.risks.models import RiskEvent, RiskEventSignal
from app.signals.models import DataSource, RawSignal

logger = logging.getLogger("risks.intelligence")

#: 送给 LLM 的信源正文上限；超长公告截断，避免单次请求体积失控。
MAX_CONTENT_CHARS = 4000


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


async def generate_intelligence_report(
    session: Session,
    event: RiskEvent,
    category: IntelligenceCategory,
    *,
    provider: IntelligenceProvider | None = None,
) -> IntelligenceReport | None:
    """生成并落库一份情报报告（成功 ready / 失败 failed+error）。

    事件无可用信号时返回 None（不写 failed 行：这不是模型故障，只是没有可解读
    的公开文本，重试也不会改变）。
    """
    payload = build_intelligence_input(session, event, category)
    if payload is None:
        return None
    active = provider or build_intelligence_provider(get_ai_settings())
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
            provider=active.provider_name,
            model=active.model,
            prompt_version=INTELLIGENCE_PROMPT_VERSION,
            status="failed",
            suggestions=[],
        )
        session.add(report)

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
    report.generated_at = datetime.now(UTC)
    session.commit()
    session.refresh(report)
    return report
