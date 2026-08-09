"""可视化规则工作台 API。

提供维度列表/详情、启停与参数更新（写入 rule_dimension_configs，引擎热更新）、
以及沙箱测试（构造样例事件不落库评估）。规则引擎核心逻辑只读不改，
业务规则的维护全部通过这些接口落到 DB 配置。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.schemas import SignalAnalysisResult
from app.database import get_session
from app.risks.engine.config import ALL_COLUMNS
from app.risks.engine.engine import evaluate_event
from app.risks.engine.registry import RuntimeDimension, load_dimensions
from app.risks.models import RiskAlert, RuleDimensionConfig
from app.risks.workbench_schemas import (
    DimensionRead,
    DimensionToggle,
    DimensionUpdate,
    SandboxRequest,
)
from app.security import require_admin

router = APIRouter(prefix="/api/v1/rule-engine", tags=["规则引擎工作台"])
SessionDependency = Annotated[Session, Depends(get_session)]
AdminDependency = Annotated[str, Depends(require_admin)]

_EVENT_TYPE_LABELS = {
    "weather": "天气",
    "geological": "地质灾害",
    "logistics": "物流",
    "trade_policy": "贸易政策",
    "geopolitical": "地缘政治",
    "corporate": "企业经营",
    "judicial": "司法",
    "compliance": "合规",
    "other": "其他",
}
_EVENT_SUBTYPE_LABELS = {
    "weather_alert": "气象预警",
    "geological_hazard": "地质灾害",
    "armed_conflict": "武装冲突",
    "sanctions": "制裁",
    "export_control": "出口管制",
    "political_instability": "政治不稳定",
    "public_security": "公共安全",
    "trade_tariff": "关税与一般贸易摩擦",
    "regulatory_change": "监管政策变化",
    "raw_material_shortage": "原材料短缺",
    "transport_disruption": "运输中断",
    "corporate_distress": "企业经营异常",
    "judicial_case": "司法案件",
    "compliance_violation": "合规违规",
    "other": "其他",
}


def _scoring_summary(dim: RuntimeDimension) -> dict[str, object]:
    s = dim.scoring
    return {
        "rule_version": s.rule_version,
        "severity_scores": s.severity_scores,
        "association_scores": s.association_scores,
        "credibility_weight": s.credibility_weight,
        "timeliness_with_date": s.timeliness_with_date,
        "timeliness_without_date": s.timeliness_without_date,
        "product_relevance_score": s.product_relevance_score,
        "p1_min": s.p1_min,
        "p2_min": s.p2_min,
        "p3_min": s.p3_min,
        "strong_match_types": sorted(s.strong_match_types),
        "alert_expiry_days": s.alert_expiry_days,
        "forced_rules": [
            {
                "name": rule.name,
                "description": rule.description,
                "event_types": list(rule.event_types),
                "event_subtypes": list(rule.event_subtypes),
                "match_types": list(rule.match_types),
                "forced_level": rule.forced_level,
                "reason": rule.reason,
            }
            for rule in s.forced_rules
        ],
    }


def _active_alert_counts(session: Session) -> dict[str, int]:
    dimension_col = RiskAlert.score_detail["dimension"].astext.label("dimension")
    rows = session.execute(
        select(dimension_col, func.count())
        .where(RiskAlert.status == "current")
        .group_by(dimension_col)
    ).all()
    return {str(dim): int(count) for dim, count in rows if dim is not None}


def _to_read(
    dim: RuntimeDimension, override_keys: set[str], counts: dict[str, int]
) -> DimensionRead:
    return DimensionRead(
        key=dim.key,
        label=dim.config.label,
        description=dim.config.description,
        content_items=list(dim.config.content_items),
        data_sources=[
            {"code": source.code, "name": source.name, "status": source.status}
            for source in dim.config.data_sources
        ],
        event_types=list(dim.config.event_types),
        match_columns=list(dim.config.match_columns),
        enabled=dim.enabled,
        has_override=dim.key in override_keys,
        active_alerts=counts.get(dim.key, 0),
        scoring=_scoring_summary(dim),
    )


def _load_state(
    session: Session,
) -> tuple[list[RuntimeDimension], set[str], dict[str, int]]:
    dimensions = load_dimensions(session)
    override_keys = set(session.scalars(select(RuleDimensionConfig.key)))
    counts = _active_alert_counts(session)
    return dimensions, override_keys, counts


@router.get("/dimensions", response_model=list[DimensionRead])
def list_dimensions(session: SessionDependency) -> list[DimensionRead]:
    dimensions, override_keys, counts = _load_state(session)
    return [_to_read(dim, override_keys, counts) for dim in dimensions]


@router.get("/dimensions/{key}", response_model=DimensionRead)
def get_dimension(key: str, session: SessionDependency) -> DimensionRead:
    dimensions, override_keys, counts = _load_state(session)
    dim = next((d for d in dimensions if d.key == key), None)
    if dim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="维度不存在")
    return _to_read(dim, override_keys, counts)


@router.put("/dimensions/{key}", response_model=DimensionRead)
def update_dimension(
    key: str, payload: DimensionUpdate, session: SessionDependency, _admin: AdminDependency
) -> DimensionRead:
    dimensions, _, _ = _load_state(session)
    base = next((d for d in dimensions if d.key == key), None)
    if base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="维度不存在")

    row = session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == key)
    )
    if row is None:
        row = RuleDimensionConfig(key=key, label=base.config.label, enabled=base.enabled)
        session.add(row)
        session.flush()
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.config is not None:
        merged = dict(row.config or {})
        merged.update(payload.config.model_dump(exclude_none=True))
        p1_value = merged.get("p1_min", base.scoring.p1_min)
        p2_value = merged.get("p2_min", base.scoring.p2_min)
        p3_value = merged.get("p3_min", base.scoring.p3_min)
        p1_min = p1_value if isinstance(p1_value, int) else base.scoring.p1_min
        p2_min = p2_value if isinstance(p2_value, int) else base.scoring.p2_min
        p3_min = p3_value if isinstance(p3_value, int) else base.scoring.p3_min
        if not p1_min > p2_min > p3_min:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="等级阈值必须满足 p1_min > p2_min > p3_min",
            )
        row.config = merged
    session.commit()
    session.expire_all()

    dimensions, override_keys, counts = _load_state(session)
    dim = next(d for d in dimensions if d.key == key)
    return _to_read(dim, override_keys, counts)


@router.post("/dimensions/{key}/toggle", response_model=DimensionRead)
def toggle_dimension(
    key: str, payload: DimensionToggle, session: SessionDependency, _admin: AdminDependency
) -> DimensionRead:
    return update_dimension(key, DimensionUpdate(enabled=payload.enabled), session, _admin)


@router.get("/match-columns")
def list_match_columns() -> dict[str, object]:
    """匹配柱与事件类型选项，供工作台表单渲染。"""
    return {
        "match_columns": list(ALL_COLUMNS),
        "event_types": [
            {"value": value, "label": label}
            for value, label in _EVENT_TYPE_LABELS.items()
        ],
        "event_subtypes": [
            {"value": value, "label": label}
            for value, label in _EVENT_SUBTYPE_LABELS.items()
        ],
    }


@router.post("/test")
def sandbox_test(payload: SandboxRequest, session: SessionDependency) -> dict[str, object]:
    """沙箱：构造样例事件，不落库评估维度命中与评分明细。"""
    result = SignalAnalysisResult(
        event_type=payload.event_type,
        event_subtype=payload.event_subtype,
        suggested_severity=payload.severity,
        organizations=payload.organizations,
        locations=payload.locations,
        affected_products=payload.affected_products,
        affected_industries=payload.affected_industries,
        summary_zh=payload.summary,
        evidence_sentences=[payload.summary],
        confidence=1.0,
    )
    return evaluate_event(
        session,
        result,
        credibility=payload.credibility,
        has_published_at=payload.has_published_at,
    )
