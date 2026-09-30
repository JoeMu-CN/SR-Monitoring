"""风险事件归并、供应商匹配、评分与提醒处理。"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.agent.tyc_report import TycRiskReport
from app.ai.models import AIAnalysisRecord
from app.ai.schemas import OrganizationReference, SignalAnalysisResult
from app.risks.engine.alert_persistence import AlertValues, upsert_alert
from app.risks.engine.config import (
    COLUMN_COUNTRY,
    COLUMN_ENTITY,
    COLUMN_INDUSTRY,
    COLUMN_LOCATION,
    COLUMN_PRODUCT,
)
from app.risks.engine.event_identity import find_or_create_event, persist_event_facts
from app.risks.engine.matching import (
    MATCH_ORDER,
    MatchCandidate,
    match_countries,
    match_entities,
    match_industries,
    match_locations,
    match_products,
)
from app.risks.engine.registry import RuntimeDimension, load_dimensions
from app.risks.models import RiskEventSignal, SupplierEventMatch
from app.risks.schemas import RiskProcessResult
from app.risks.scoring import (
    LlmSuggestion,
    ScoringSettings,
    compute_score,
    load_scoring_settings,
    resolve_level,
)
from app.risks.validity import (
    InactiveRiskSignalError,
    expire_alerts,
    is_raw_signal_effective,
    refresh_event_support,
)
from app.signals.models import DataSource, RawSignal
from app.suppliers.models import Supplier

logger = logging.getLogger(__name__)

MatcherFn = Callable[
    [Session, SignalAnalysisResult, list[Supplier], dict[str, int], dict[int, MatchCandidate]],
    None,
]

MATCHERS: dict[str, MatcherFn] = {
    COLUMN_ENTITY: match_entities,
    COLUMN_LOCATION: match_locations,
    COLUMN_PRODUCT: match_products,
    COLUMN_COUNTRY: match_countries,
    COLUMN_INDUSTRY: match_industries,
}


def match_type(types: set[str]) -> str:
    return "+".join(sorted(types, key=MATCH_ORDER.__getitem__))


def load_suppliers(session: Session) -> list[Supplier]:
    return list(
        session.scalars(
            select(Supplier)
            .where(Supplier.enabled.is_(True))
            .options(
                selectinload(Supplier.aliases),
                selectinload(Supplier.sites),
                selectinload(Supplier.products),
            )
        ).unique()
    )


def resolve_dimension(
    dimensions: list[RuntimeDimension], event_type: str
) -> RuntimeDimension | None:
    matches = [
        dimension
        for dimension in dimensions
        if dimension.enabled and dimension.handles(event_type)
    ]
    if len(matches) > 1:
        logger.warning(
            "事件类型 %s 被多个启用维度声明（%s），取第一个 %s",
            event_type,
            ", ".join(dimension.key for dimension in matches),
            matches[0].key,
        )
    return matches[0] if matches else None


def match_suppliers(
    session: Session,
    result: SignalAnalysisResult,
    scoring: ScoringSettings | None = None,
) -> list[MatchCandidate]:
    """兼容包装：主体、地点与产品三柱匹配。"""
    resolved_scoring = scoring or load_scoring_settings()
    suppliers = load_suppliers(session)
    matches: dict[int, MatchCandidate] = {}
    match_entities(session, result, suppliers, resolved_scoring.association_scores, matches)
    match_locations(session, result, suppliers, resolved_scoring.association_scores, matches)
    match_products(session, result, suppliers, resolved_scoring.association_scores, matches)
    return [matches[key] for key in sorted(matches)]


def _upsert_match(
    session: Session, event_id: int, candidate: MatchCandidate
) -> SupplierEventMatch:
    match = session.scalar(
        select(SupplierEventMatch).where(
            SupplierEventMatch.supplier_id == candidate.supplier.id,
            SupplierEventMatch.event_id == event_id,
        )
    )
    if match is None:
        match = SupplierEventMatch(
            supplier_id=candidate.supplier.id,
            event_id=event_id,
            match_type=match_type(candidate.match_types),
            score=candidate.association_score,
            reasons=candidate.reasons,
            evidence=candidate.evidence,
        )
        session.add(match)
        session.flush()
        return match
    match.match_type = match_type(set(match.match_type.split("+")) | candidate.match_types)
    match.score = max(match.score, candidate.association_score)
    match.reasons = list(dict.fromkeys([*match.reasons, *candidate.reasons]))
    match.evidence = [
        *match.evidence,
        *(item for item in candidate.evidence if item not in match.evidence),
    ]
    return match


def _resolve_profile_identity(
    signal: RawSignal, result: SignalAnalysisResult
) -> tuple[SignalAnalysisResult, str | None]:
    """supplier_profile 周度报告：从 raw_data 确定性注入主体与稳定身份。

    仅当 ``signal.raw_data.report_kind == "supplier_profile"`` 时生效：解析
    完整 TycRiskReport 合同（缺字段/空白 supplier_code 明确失败，绝不回退
    普通逻辑造成错误合并），用报告 company_name/credit_code 覆盖
    organizations（registry_no 仅非空时填充），并返回恒为非空且不含周次的
    身份覆盖 ``supplier_profile:<supplier_code>``。其余信号原样返回。
    """
    raw_data = signal.raw_data
    if not isinstance(raw_data, dict) or raw_data.get("report_kind") != "supplier_profile":
        return result, None
    try:
        report = TycRiskReport.model_validate(raw_data)
    except ValidationError as exc:
        raise ValueError("supplier_profile 报告信号缺少必要字段，拒绝处理") from exc
    organizations = [
        OrganizationReference(
            name=report.company_name,
            registry_no=report.credit_code or None,
        )
    ]
    return (
        result.model_copy(update={"organizations": organizations}),
        f"supplier_profile:{report.supplier_code}",
    )


def process_event(
    session: Session,
    signal: RawSignal,
    analysis: AIAnalysisRecord,
    *,
    now_utc: datetime | None = None,
) -> RiskProcessResult:
    """仅以当前有效信号归并事件、匹配、评分并生成提醒。"""
    now = now_utc or datetime.now(UTC)
    if analysis.status != "succeeded" or analysis.result is None:
        raise ValueError("AI 分析尚未成功")
    if not is_raw_signal_effective(signal, now_utc=now):
        raise InactiveRiskSignalError(signal.id, signal.validity_state)
    result = SignalAnalysisResult.model_validate(analysis.result)
    # 画像报告：服务端确定性注入主体与稳定身份（其余信号原样通过）。
    result, identity_override = _resolve_profile_identity(signal, result)
    event, event_created = find_or_create_event(
        session, result, identity_override=identity_override
    )
    # 画像路径侧表随当前周快照精确替换；普通事件保持既有增量补齐语义。
    persist_event_facts(
        session, event, result, replace=identity_override is not None
    )
    linked_id = session.scalar(
        insert(RiskEventSignal)
        .values(event_id=event.id, signal_id=signal.id)
        .on_conflict_do_nothing(
            index_elements=[RiskEventSignal.event_id, RiskEventSignal.signal_id]
        )
        .returning(RiskEventSignal.signal_id)
    )
    support = refresh_event_support(session, event, now_utc=now)
    source = session.get(DataSource, signal.source_id)
    assert source is not None
    dimension = resolve_dimension(load_dimensions(session), result.event_type)
    if dimension is None:
        analysis.needs_review = True
        reason = f"没有启用的维度接管事件类型 {result.event_type}"
        analysis.review_reason = "；".join(
            item for item in (analysis.review_reason, reason) if item
        )

    alert_ids: list[int] = []
    if dimension is not None:
        scoring = dimension.scoring
        matches: dict[int, MatchCandidate] = {}
        suppliers = load_suppliers(session)
        for column in dimension.config.match_columns:
            matcher = MATCHERS.get(column)
            if matcher is not None:
                matcher(session, result, suppliers, scoring.association_scores, matches)
        for candidate in (matches[key] for key in sorted(matches)):
            match = _upsert_match(session, event.id, candidate)
            product_relevant = any(
                item.get("object_type") == "product" for item in match.evidence
            )
            score, score_detail = compute_score(
                scoring,
                result.suggested_severity,
                match.score,
                source.credibility,
                signal.published_at is not None,
                product_relevant,
            )
            score_detail["dimension"] = dimension.key
            # 唯一评分链：base → LLM 采纳（仅确认或提升）→ 上限 → 强制规则。
            level, score = resolve_level(
                scoring,
                LlmSuggestion(
                    suggested_level=result.suggested_level,
                    confidence=result.confidence,
                    rationale=result.level_rationale,
                ),
                event.event_type,
                match.match_type,
                score,
                score_detail,
                event_subtype=result.event_subtype,
            )
            alert = upsert_alert(
                session,
                match,
                AlertValues(
                    level=level,
                    score=score,
                    score_detail=score_detail,
                    expires_at=support.expires_at,
                    now_utc=now,
                ),
            )
            alert_ids.append(alert.id)
        if not alert_ids:
            analysis.needs_review = True
            reason = "AI 分析成功但未匹配到供应商，未生成风险提醒"
            analysis.review_reason = "；".join(
                item for item in (analysis.review_reason, reason) if item
            )
    expire_alerts(session, now_utc=now)
    session.commit()
    return RiskProcessResult(
        signal_id=signal.id,
        event_id=event.id,
        event_created=event_created,
        signal_linked=linked_id is not None,
        alert_ids=alert_ids,
    )
