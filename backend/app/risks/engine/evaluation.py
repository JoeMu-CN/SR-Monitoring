"""规则工作台的不落库事件评估。"""

from sqlalchemy.orm import Session

from app.ai.schemas import SignalAnalysisResult
from app.risks.engine.matching import MatchCandidate
from app.risks.engine.processing import MATCHERS, load_suppliers, match_type, resolve_dimension
from app.risks.engine.registry import load_dimensions
from app.risks.scoring import apply_forced_rules, apply_level_cap, compute_level, compute_score


def evaluate_event(
    session: Session,
    result: SignalAnalysisResult,
    *,
    credibility: int = 80,
    has_published_at: bool = True,
) -> dict[str, object]:
    """按当前维度配置执行匹配与评分，但不落库。"""
    dimension = resolve_dimension(load_dimensions(session), result.event_type)
    if dimension is None:
        return {
            "dimension": None,
            "message": f"没有启用的维度接管事件类型 {result.event_type}",
            "candidates": [],
        }
    scoring = dimension.scoring
    suppliers = load_suppliers(session)
    matches: dict[int, MatchCandidate] = {}
    for column in dimension.config.match_columns:
        matcher = MATCHERS.get(column)
        if matcher is not None:
            matcher(session, result, suppliers, scoring.association_scores, matches)

    candidates: list[dict[str, object]] = []
    for candidate in (matches[key] for key in sorted(matches)):
        candidate_match_type = match_type(candidate.match_types)
        product_relevant = any(
            item.get("object_type") == "product" for item in candidate.evidence
        )
        score, score_detail = compute_score(
            scoring,
            result.suggested_severity,
            candidate.association_score,
            credibility,
            has_published_at,
            product_relevant,
        )
        score_detail["dimension"] = dimension.key
        level = apply_level_cap(
            scoring, compute_level(scoring, score), candidate_match_type, score_detail
        )
        level, score = apply_forced_rules(
            scoring,
            result.event_type,
            candidate_match_type,
            level,
            score,
            score_detail,
            event_subtype=result.event_subtype,
        )
        candidates.append(
            {
                "supplier_id": candidate.supplier.id,
                "supplier_name": candidate.supplier.legal_name,
                "match_type": candidate_match_type,
                "association_score": candidate.association_score,
                "reasons": candidate.reasons,
                "score": score,
                "level": level,
                "score_detail": score_detail,
            }
        )

    def sort_key(item: dict[str, object]) -> tuple[int, str]:
        score = item["score"]
        return (-(score if isinstance(score, int) else 0), str(item["supplier_name"]))

    candidates.sort(key=sort_key)
    return {
        "dimension": {
            "key": dimension.key,
            "label": dimension.config.label,
            "match_columns": list(dimension.config.match_columns),
        },
        "candidates": candidates,
    }
