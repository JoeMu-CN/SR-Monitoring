"""可视化规则工作台 API。

提供维度列表/详情、启停与参数更新（写入 rule_dimension_configs，引擎热更新）、
以及沙箱测试（构造样例事件不落库评估）。规则引擎核心逻辑只读不改，
业务规则的维护全部通过这些接口落到 DB 配置。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.ai.schemas import SignalAnalysisResult
from app.auth.models import User
from app.auth.security import (
    PERM_RULE_MANAGE,
    PERM_RULE_SUMMARY_VIEW,
    require_permission,
    verify_csrf,
)
from app.database import get_session
from app.risks.engine.config import ALL_COLUMNS
from app.risks.engine.engine import evaluate_event
from app.risks.engine.registry import RuntimeDimension, load_dimensions
from app.risks.models import (
    RiskAlert,
    RiskEvent,
    RiskEventSignal,
    RuleDimensionConfig,
    SupplierEventMatch,
)
from app.risks.query_validity import (
    current_alert_condition,
    valid_signal_condition,
)
from app.risks.workbench_schemas import (
    DimensionInputSourceRead,
    DimensionInputsRead,
    DimensionRead,
    DimensionSourceRead,
    DimensionToggle,
    DimensionUpdate,
    SandboxRequest,
)
from app.signals.models import CollectionRun, DataSource, RawSignal

router = APIRouter(prefix="/api/v1/rule-engine", tags=["规则引擎工作台"])
SessionDependency = Annotated[Session, Depends(get_session)]
RuleSummaryView = Annotated[User, Depends(require_permission(PERM_RULE_SUMMARY_VIEW))]
RuleManage = Annotated[User, Depends(require_permission(PERM_RULE_MANAGE))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]

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


@dataclass(frozen=True)
class _SourceRealState:
    """data_sources 表实时状态；linked=false 时全部实时字段为 None。"""

    linked: bool
    enabled: bool | None
    adapter_status: str | None
    last_collected_at: datetime | None
    valid_signal_count: int | None


def _source_status_map(
    session: Session, codes: set[str]
) -> dict[str, _SourceRealState]:
    """按 code 批量查询 DataSource 构建实时状态映射。

    查询次数只取决于信源总数（固定常数），不随维度数增长：
    1 次查 DataSource 行、1 次按 source_id 分组取最近采集时间、
    按 signal_validity_days 分组各 1 次统计有效信号数（判活谓词复用
    query_validity.valid_signal_condition，与任务 9 统一口径）。
    """
    if not codes:
        return {}
    now_utc = datetime.now(UTC)
    sources = {
        s.code: s
        for s in session.scalars(select(DataSource).where(DataSource.code.in_(codes)))
    }
    if not sources:
        return {code: _SourceRealState(False, None, None, None, None) for code in codes}

    source_ids = {s.id for s in sources.values()}

    last_collected: dict[int, datetime] = {}
    rows = session.execute(
        select(CollectionRun.source_id, func.max(CollectionRun.finished_at))
        .where(CollectionRun.source_id.in_(source_ids))
        .group_by(CollectionRun.source_id)
    ).all()
    for source_id, finished_at in rows:
        if finished_at is not None:
            last_collected[int(source_id)] = finished_at

    # 同 signal_validity_days 的信源判活条件一致，按组各一次查询
    groups: dict[int | None, list[DataSource]] = {}
    for src in sources.values():
        groups.setdefault(src.signal_validity_days, []).append(src)
    valid_counts: dict[int, int] = {}
    for group in groups.values():
        representative = group[0]
        condition = valid_signal_condition(representative, now_utc=now_utc)
        count_rows = session.execute(
            select(RawSignal.source_id, func.count())
            .where(RawSignal.source_id.in_([s.id for s in group]), condition)
            .group_by(RawSignal.source_id)
        ).all()
        for source_id, count in count_rows:
            valid_counts[int(source_id)] = int(count)

    result: dict[str, _SourceRealState] = {}
    for code in codes:
        matched = sources.get(code)
        if matched is None:
            result[code] = _SourceRealState(False, None, None, None, None)
        else:
            result[code] = _SourceRealState(
                linked=True,
                enabled=matched.enabled,
                adapter_status=matched.adapter_status,
                last_collected_at=last_collected.get(matched.id),
                valid_signal_count=valid_counts.get(matched.id, 0),
            )
    return result


def _to_read(
    dim: RuntimeDimension,
    override_keys: set[str],
    counts: dict[str, int],
    source_map: dict[str, _SourceRealState],
) -> DimensionRead:
    data_sources = [
        DimensionSourceRead(
            code=source.code,
            name=source.name,
            declared_status=source.status,
            linked=source_map[source.code].linked,
            enabled=source_map[source.code].enabled,
            adapter_status=source_map[source.code].adapter_status,
            last_collected_at=source_map[source.code].last_collected_at,
            valid_signal_count=source_map[source.code].valid_signal_count,
        )
        for source in dim.config.data_sources
    ]
    return DimensionRead(
        key=dim.key,
        label=dim.config.label,
        description=dim.config.description,
        content_items=list(dim.config.content_items),
        data_sources=data_sources,
        event_types=list(dim.config.event_types),
        match_columns=list(dim.config.match_columns),
        enabled=dim.enabled,
        has_override=dim.key in override_keys,
        active_alerts=counts.get(dim.key, 0),
        scoring=_scoring_summary(dim),
    )


def _load_state(
    session: Session,
) -> tuple[list[RuntimeDimension], set[str], dict[str, int], dict[str, _SourceRealState]]:
    dimensions = load_dimensions(session)
    override_keys = set(session.scalars(select(RuleDimensionConfig.key)))
    counts = _active_alert_counts(session)
    source_codes = {s.code for d in dimensions for s in d.config.data_sources}
    source_map = _source_status_map(session, source_codes)
    return dimensions, override_keys, counts, source_map


@router.get("/dimensions", response_model=list[DimensionRead])
def list_dimensions(
    session: SessionDependency, _user: RuleSummaryView
) -> list[DimensionRead]:
    dimensions, override_keys, counts, source_map = _load_state(session)
    return [_to_read(dim, override_keys, counts, source_map) for dim in dimensions]


@router.get("/dimensions/{key}", response_model=DimensionRead)
def get_dimension(
    key: str, session: SessionDependency, _user: RuleSummaryView
) -> DimensionRead:
    dimensions, override_keys, counts, source_map = _load_state(session)
    dim = next((d for d in dimensions if d.key == key), None)
    if dim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="维度不存在")
    return _to_read(dim, override_keys, counts, source_map)


@router.get("/dimensions/{key}/inputs", response_model=DimensionInputsRead)
def get_dimension_inputs(
    key: str,
    session: SessionDependency,
    _user: RuleSummaryView,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> DimensionInputsRead:
    """维度输入健康度反查。

    declared_* 复用任务 13 的 _source_status_map join 结果；
    observed 从该维度实际接管的 current 提醒反查其依赖的原始信号，
    按 source_id 聚合（alert → SupplierEventMatch → RiskEvent →
    RiskEventSignal → RawSignal → DataSource）。未声明但实际有产出的
    信源如实返回、不隐藏。
    """
    dimensions, _, _, _ = _load_state(session)
    dim = next((d for d in dimensions if d.key == key), None)
    if dim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="维度不存在")

    declared_codes = {s.code for s in dim.config.data_sources}
    source_map = _source_status_map(session, declared_codes)
    declared_total = len(declared_codes)
    declared_linked = sum(1 for s in source_map.values() if s.linked)
    declared_enabled = sum(
        1 for s in source_map.values() if s.linked and s.enabled
    )

    now_utc = datetime.now(UTC)
    cutoff = now_utc - timedelta(days=days)
    alert_condition = and_(
        current_alert_condition(now_utc),
        RiskAlert.score_detail["dimension"].astext == key,
        RiskAlert.updated_at >= cutoff,
    )
    rows = session.execute(
        select(
            DataSource.code,
            DataSource.name,
            func.count(RawSignal.id),
            func.max(func.coalesce(RawSignal.published_at, RawSignal.collected_at)),
        )
        .select_from(RiskAlert)
        .join(SupplierEventMatch, SupplierEventMatch.id == RiskAlert.match_id)
        .join(RiskEvent, RiskEvent.id == SupplierEventMatch.event_id)
        .join(RiskEventSignal, RiskEventSignal.event_id == RiskEvent.id)
        .join(RawSignal, RawSignal.id == RiskEventSignal.signal_id)
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(alert_condition)
        .group_by(DataSource.code, DataSource.name)
    ).all()

    observed = [
        DimensionInputSourceRead(
            code=code,
            name=name,
            signal_count=int(count),
            latest_at=latest_at,
        )
        for code, name, count, latest_at in rows
    ]
    has_input = declared_enabled > 0 and bool(observed)
    return DimensionInputsRead(
        declared_total=declared_total,
        declared_linked=declared_linked,
        declared_enabled=declared_enabled,
        observed=observed,
        has_input=has_input,
    )


@router.put("/dimensions/{key}", response_model=DimensionRead)
def update_dimension(
    key: str,
    payload: DimensionUpdate,
    session: SessionDependency,
    _user: RuleManage,
    _csrf: CsrfGuard,
) -> DimensionRead:
    dimensions, _, _, _ = _load_state(session)
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

    dimensions, override_keys, counts, source_map = _load_state(session)
    dim = next(d for d in dimensions if d.key == key)
    return _to_read(dim, override_keys, counts, source_map)


@router.post("/dimensions/{key}/toggle", response_model=DimensionRead)
def toggle_dimension(
    key: str,
    payload: DimensionToggle,
    session: SessionDependency,
    _user: RuleManage,
    _csrf: CsrfGuard,
) -> DimensionRead:
    return update_dimension(
        key, DimensionUpdate(enabled=payload.enabled), session, _user, _csrf
    )


@router.get("/match-columns")
def list_match_columns(_user: RuleSummaryView) -> dict[str, object]:
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
def sandbox_test(
    payload: SandboxRequest,
    session: SessionDependency,
    _user: RuleManage,
    _csrf: CsrfGuard,
) -> dict[str, object]:
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
