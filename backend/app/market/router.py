"""行情指标只读视图与运行配置 API（``/api/v1/market``）。

只读视图完全复用既有确定性口径，不重算阈值：

- ``load_series`` 取观察点、``evaluate_series`` 出判定与窗口统计，三态映射为
  ``tripped`` / ``accumulating``（未触发且观察点未达 ``min_observations``）/
  ``normal``。自己再实现一遍比较会在"阈值含等于"这类口径改动时立刻漂移。
- ``latest_alert_id`` 走 ``RiskAlert → SupplierEventMatch → RiskEventSignal →
  RawSignal``，按 ``raw_data`` 的 JSONB 绑定键命中该指标后取 ``status=current``
  的最近一条；绑定键比较与 ``load_series`` 同源（``registry._matches``），否则
  指标序列能算出来、提醒却永远挂不上。

配置读写与 ``signal-filter`` 同模式：合并语义、越界与未知指标 422、写后立即
失效配置缓存、DELETE 删除配置行回退代码默认。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.security import (
    PERM_RISK_VIEW,
    PERM_RULE_MANAGE,
    PERM_SOURCE_STATUS_VIEW,
    require_permission,
    verify_csrf,
)
from app.database import get_session
from app.market.config_store import (
    MARKET_CONFIG_KEY,
    MarketConfig,
    default_market_config,
    invalidate_market_config_cache,
    load_market_config,
)
from app.market.evaluation import evaluate_series, load_series
from app.market.registry import (
    INDICATORS_BY_CODE,
    IndicatorSpec,
    # 绑定语义必须与解析入口同源：此处换一套比较会让提醒挂到别的指标上。
    _matches,
    _normalize_extra,
)
from app.market.schemas import (
    Direction,
    IndicatorItemRead,
    IndicatorLatestRead,
    IndicatorListResponse,
    IndicatorPointRead,
    IndicatorSeriesResponse,
    IndicatorStatsRead,
    IndicatorStatus,
    IndicatorThresholdsRead,
    MarketConfigIndicatorRead,
    MarketConfigRead,
    MarketConfigUpdate,
    Severity,
    SeverityBandRead,
)
from app.risks.models import (
    RiskAlert,
    RiskEventSignal,
    RuleDimensionConfig,
    SupplierEventMatch,
)
from app.signals.models import DataSource, RawSignal

router = APIRouter(prefix="/api/v1/market", tags=["行情指标"])
SessionDependency = Annotated[Session, Depends(get_session)]
RiskView = Annotated[User, Depends(require_permission(PERM_RISK_VIEW))]
SourceStatusView = Annotated[User, Depends(require_permission(PERM_SOURCE_STATUS_VIEW))]
RuleManage = Annotated[User, Depends(require_permission(PERM_RULE_MANAGE))]
CsrfGuard = Annotated[None, Depends(verify_csrf)]

#: 响应里回带的观察点个数上限（按时间升序取最近 N 个；不做分页）。
SERIES_POINTS: Final = 30

_CONFIG_LABEL: Final = "行情指标运行配置"
_INDICATORS_KEY: Final = "indicators"


# ---- 指标视图 --------------------------------------------------------


@router.get("/indicators", response_model=IndicatorListResponse)
def list_market_indicators(
    session: SessionDependency, _user: RiskView
) -> IndicatorListResponse:
    """启用中行情指标的只读视图：三态判定 + 窗口统计 + 阈值 + 最近观察点。

    被配置停用的指标视同未登记（与 ``resolve_indicator`` 同口径），不在此返回。
    """
    now = datetime.now(UTC)
    specs = load_market_config(session).enabled_specs()
    sources = _source_index(session, {spec.source_code for spec in specs})
    alerts = _latest_alert_ids(session, specs)
    items = [
        _indicator_item(
            session, spec, sources.get(spec.source_code), alerts.get(spec.code), now=now
        )
        for spec in specs
    ]
    return IndicatorListResponse(generated_at=now, items=items)


@router.get("/indicators/{code}/series", response_model=IndicatorSeriesResponse)
def market_indicator_series(
    code: str,
    session: SessionDependency,
    _user: RiskView,
    days: int = Query(default=90, ge=1, le=90),
) -> IndicatorSeriesResponse:
    """单指标观察点序列（只读记录，不含判定结论）。

    放大走势图按需拉取：``load_series`` 本身按 90 天保留期读取，这里再按
    ``days`` 截断。未登记或运行期停用的指标与列表视图同口径（404），
    避免"详情能看、列表里没有"的两套可见性。
    """
    now = datetime.now(UTC)
    spec = load_market_config(session).spec_for(code)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未登记或已停用的行情指标：{code}",
        )
    source_id = session.scalar(
        select(DataSource.id).where(DataSource.code == spec.source_code)
    )
    series = (
        load_series(session, spec, source_id=source_id, now=now)
        if source_id is not None
        else ()
    )
    cutoff = now - timedelta(days=days)
    return IndicatorSeriesResponse(
        code=spec.code,
        label=spec.label,
        unit=spec.unit,
        days=days,
        points=[
            IndicatorPointRead(observed_at=point.collected_at, value=point.value)
            for point in series
            if point.collected_at >= cutoff
        ],
    )


def _source_index(
    session: Session, codes: set[str]
) -> dict[str, tuple[int, str]]:
    """信源代码 → (id, 名称)；库中缺失的代码不在结果里（视作无信源）。"""
    if not codes:
        return {}
    rows = session.execute(
        select(DataSource.id, DataSource.code, DataSource.name).where(
            DataSource.code.in_(sorted(codes))
        )
    )
    return {code: (source_id, name) for source_id, code, name in rows}


def _indicator_item(
    session: Session,
    spec: IndicatorSpec,
    source: tuple[int, str] | None,
    latest_alert_id: int | None,
    *,
    now: datetime,
) -> IndicatorItemRead:
    """单指标视图条目。信源缺失时序列为空，三态自然落在 accumulating。"""
    series = (
        load_series(session, spec, source_id=source[0], now=now)
        if source is not None
        else ()
    )
    verdict = evaluate_series(spec, series)
    stats = verdict.stats
    indicator_status: IndicatorStatus
    if verdict.triggered:
        indicator_status = "tripped"
    elif stats.observations < spec.min_observations:
        indicator_status = "accumulating"
    else:
        indicator_status = "normal"
    latest = series[-1] if series else None
    return IndicatorItemRead(
        code=spec.code,
        label=spec.label,
        source_code=spec.source_code,
        source_name=source[1] if source is not None else None,
        unit=spec.unit,
        latest=IndicatorLatestRead(
            observed_at=latest.collected_at if latest is not None else None,
            value=latest.value if latest is not None else None,
            change_pct=latest.change_pct if latest is not None else None,
        ),
        stats=IndicatorStatsRead(
            observations=stats.observations,
            sustained_change_pct=stats.cum_pct,
            shock_max_day_pct=stats.max_day_pct,
        ),
        thresholds=_thresholds(spec),
        status=indicator_status,
        series=[
            IndicatorPointRead(observed_at=point.collected_at, value=point.value)
            for point in series[-SERIES_POINTS:]
        ],
        latest_alert_id=latest_alert_id,
    )


def _latest_alert_ids(
    session: Session, specs: tuple[IndicatorSpec, ...]
) -> dict[str, int]:
    """每个指标最近一条 ``status=current`` 提醒的 id（无则缺失）。

    ``raw_data`` 的绑定键过滤下推到 JSONB 比较（``->>`` 与 ``_match_key`` 的
    字符串化口径一致），再用 ``_matches`` 复核归属，避免一条信号同时挂多个事件
    时张冠李戴。
    """
    if not specs:
        return {}
    conditions = [
        and_(
            *(
                RawSignal.raw_data["source_extra"][key].astext == expected
                for key, expected in spec.extra_match.items()
            )
        )
        for spec in specs
    ]
    rows = session.execute(
        select(RiskAlert.id, RawSignal.raw_data)
        .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
        .join(RiskEventSignal, RiskEventSignal.event_id == SupplierEventMatch.event_id)
        .join(RawSignal, RawSignal.id == RiskEventSignal.signal_id)
        .where(RiskAlert.status == "current", or_(*conditions))
        .order_by(RiskAlert.id.desc())
    )
    found: dict[str, int] = {}
    for alert_id, raw_data in rows:
        extra = (
            _normalize_extra(raw_data.get("source_extra"))
            if isinstance(raw_data, dict)
            else None
        )
        if extra is None:
            continue
        for spec in specs:
            if spec.code not in found and _matches(spec, extra):
                found[spec.code] = alert_id
        if len(found) == len(specs):
            break
    return found


# ---- 运行配置 --------------------------------------------------------


@router.get("/config", response_model=MarketConfigRead)
def get_market_config(
    session: SessionDependency, _user: SourceStatusView
) -> MarketConfigRead:
    """读取当前生效的行情配置（DB 覆盖合并代码默认）与逐指标覆盖状态。"""
    return _config_read(session, _config_row(session))


@router.put("/config", response_model=MarketConfigRead)
def update_market_config(
    payload: MarketConfigUpdate,
    session: SessionDependency,
    _user: RuleManage,
    _csrf: CsrfGuard,
) -> MarketConfigRead:
    """按指标合并写入运行配置；注册表外的指标代码与越界值一律 422。

    合并语义：只覆盖补丁里出现且合法的字段，其余沿用当前值；空补丁是 no-op。
    写入后立即失效配置缓存，运营无需等待 TTL 即可看到新阈值。
    """
    unknown = sorted(set(payload.indicators) - set(INDICATORS_BY_CODE))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"未知行情指标代码：{unknown}",
        )
    row = _config_row(session)
    merged: dict[str, object] = dict(row.config) if row is not None else {}
    if payload.analysis_enabled is not None:
        merged["analysis_enabled"] = payload.analysis_enabled
    stored = merged.get(_INDICATORS_KEY)
    overrides: dict[str, object] = dict(stored) if isinstance(stored, dict) else {}
    for code, patch in payload.indicators.items():
        values = patch.model_dump(exclude_unset=True, mode="json")
        if not values:
            continue  # 空补丁不做任何事，也不写入空对象占位
        current = overrides.get(code)
        overrides[code] = (
            {**current, **values} if isinstance(current, dict) else dict(values)
        )
    if overrides:
        merged[_INDICATORS_KEY] = overrides
    else:
        merged.pop(_INDICATORS_KEY, None)

    if merged:
        if row is None:
            session.add(
                RuleDimensionConfig(
                    key=MARKET_CONFIG_KEY,
                    label=_CONFIG_LABEL,
                    enabled=True,
                    config=merged,
                )
            )
        else:
            row.config = merged
            row.enabled = True  # 写入即生效：停用行不因写配置而继续压制
    elif row is not None:
        session.delete(row)
    session.commit()
    invalidate_market_config_cache()
    session.expire_all()
    return _config_read(session, _config_row(session))


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
def reset_market_config(
    session: SessionDependency,
    _user: RuleManage,
    _csrf: CsrfGuard,
) -> None:
    """删除行情配置行回退代码默认；无配置行时是 no-op（不产生任何副作用）。"""
    row = _config_row(session)
    if row is not None:
        session.delete(row)
        session.commit()
    invalidate_market_config_cache()


def _config_row(session: Session) -> RuleDimensionConfig | None:
    return session.scalar(
        select(RuleDimensionConfig).where(RuleDimensionConfig.key == MARKET_CONFIG_KEY)
    )


def _thresholds(spec: IndicatorSpec) -> IndicatorThresholdsRead:
    return IndicatorThresholdsRead(
        sustained_change_pct=spec.sustained_change_pct,
        sustained_window=spec.sustained_window,
        shock_change_pct=spec.shock_change_pct,
        shock_window=spec.shock_window,
        min_observations=spec.min_observations,
        direction=cast("Direction", spec.direction),
        episode_gap=spec.episode_gap,
        severity_bands=[
            SeverityBandRead(
                threshold_pct=band.threshold_pct,
                severity=cast("Severity", band.severity),
            )
            for band in spec.severity_bands
        ],
    )


def _config_read(
    session: Session, row: RuleDimensionConfig | None
) -> MarketConfigRead:
    """按注册表顺序组装生效配置；停用项也在此列出（运营要能看到停用了什么）。

    停用指标的 ``effective`` 就是代码默认值——``config_store`` 在 ``enabled=false``
    时本就不合并阈值，因此这里如实展示默认值，并用 ``overridden`` 标出它被停用。
    """
    effective = load_market_config(session)
    indicators = [
        _config_indicator(spec, effective) for spec in default_market_config().specs.values()
    ]
    return MarketConfigRead(
        key=MARKET_CONFIG_KEY,
        source="configured" if row is not None and row.enabled else "default",
        updated_at=row.updated_at if row is not None else None,
        analysis_enabled=effective.analysis_enabled,
        indicators=indicators,
    )


def _config_indicator(
    spec: IndicatorSpec, effective: MarketConfig
) -> MarketConfigIndicatorRead:
    enabled = effective.is_enabled(spec.code)
    merged = effective.spec_for(spec.code) or spec
    return MarketConfigIndicatorRead(
        code=spec.code,
        label=merged.label,
        source_code=spec.source_code,
        unit=merged.unit,
        enabled=enabled,
        overridden=not enabled or merged != spec,
        effective=_thresholds(merged),
        defaults=_thresholds(spec),
    )