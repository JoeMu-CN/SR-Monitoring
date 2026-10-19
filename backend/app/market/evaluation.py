"""确定性行情评估器：窗口统计 → OR 触发 → 严重性分档 → episode 归并。

观察点序列来自 ``raw_signals.raw_data["source_extra"]``（行情读数的唯一读取通道，
由 ``app.signals.service`` 落库）。本模块**只读**：不写库、不调用任何模型，
产出可直接交给既有事件链（``find_or_create_event`` → 匹配 → 评分 → 提醒）的
:class:`app.ai.schemas.SignalAnalysisResult`。

三条容易静默失效的取舍在这里固化：
1. **不过滤 ``validity_state``**：被 ``until_superseded`` 替代的历史读数同样参与
   窗口——只认 ``active`` 会让同一指标今天永远只有 1 个观察点，累计口径恒不可算，
   且界面只会一直显示"数据积累中"，无从察觉配置已经错了。
2. **脏观察点只跳过该行**：``source_extra`` 不可解析、缺现值、属于别的指标的读数
   一律跳过，绝不让单条坏数据把整条序列判成失败。
3. **冲击条件不受 ``min_observations`` 约束**：只要接口给出 ``change_key``，
   单条读数即可产生事件；``min_observations`` 只约束需要回溯的持续条件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.ai.schemas import EventSubtype, EventType, Severity, SignalAnalysisResult
from app.market.registry import (
    IndicatorSpec,
    # 绑定语义（extra 归一化 + 键值比较）必须与解析入口同源：任何一处漂移都会
    # 让 load_series 恒空而指标永远停在"数据积累中"，且不产生任何错误。
    _matches,
    _normalize_extra,
)
from app.signals.models import RawSignal

#: 观察点回看天数。远大于任何指标所需的窗口长度（最大 sustained_window=20 个
#: 观察点，约 20 天），因此不会在窗口攒满前截断。
SERIES_RETENTION_DAYS: Final = 90

#: 确定性行情评估在 ``ai_analysis_records.provider`` 上的固定标识；episode 归并
#: 只认这个 provider 的记录，绝不把 LLM 分析误当行情事件。
DETERMINISTIC_PROVIDER: Final = "deterministic-market"

#: 置信度固定 0.99：口径是纯统计复算，不含任何推断成分。
DETERMINISTIC_CONFIDENCE: Final = 0.99

CONDITION_SUSTAINED: Final = "sustained"
CONDITION_SHOCK: Final = "shock"

REASON_NO_OBSERVATIONS: Final = "no_observations"
REASON_ACCUMULATING: Final = "accumulating"
REASON_BELOW_THRESHOLD: Final = "below_threshold"

# 性能备注：load_series 只用 (source_id, collected_at) 走已有索引
# ix_raw_signals_source_published 与 ix_raw_signals_validity_queue 的前缀，
# 本地规模（每信源每日 15~20 条）无需额外表达式索引。若将来单信源观察点规模
# 上到十万级，可为 ``raw_data->'source_extra'`` 建 GIN 索引并下推 extra_match。
# _prior_trigger 只扫"已触发的确定性记录"（本模块产出的分析才会落库），天然很小。


@dataclass(frozen=True, slots=True)
class Observation:
    """一个可用观察点：现值、接口给出的当日涨跌（可空）、采集时间与所属信号。"""

    signal_id: int
    collected_at: datetime
    value: float
    change_pct: float | None


@dataclass(frozen=True, slots=True)
class WindowStats:
    """尾部窗口统计结果。``cum_*`` 描述持续条件，``max_day_*`` 描述冲击条件。"""

    observations: int
    cum_pct: float | None
    cum_window: int
    base_value: float | None
    latest_value: float | None
    max_day_change: float | None
    max_day_pct: float | None


@dataclass(frozen=True, slots=True)
class MarketVerdict:
    """一次评估的判定结论（纯数据，不含数据库对象）。"""

    triggered: bool
    condition: str | None
    severity: str | None
    magnitude_pct: float | None
    trigger_change: float | None
    sustained_triggered: bool
    shock_triggered: bool
    stats: WindowStats


@dataclass(frozen=True, slots=True)
class FilteredReason:
    """未触发时的过滤原因（code 供机器判读，detail_zh 供运营展示）。"""

    code: str
    detail_zh: str


def _number(value: object) -> float | None:
    """严格数值：bool 与数字字符串都不接受（与 config_store 口径一致）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def load_series(
    session: Session,
    spec: IndicatorSpec,
    *,
    source_id: int,
    now: datetime | None = None,
) -> tuple[Observation, ...]:
    """读取指标 ``spec`` 近 90 天的观察点序列（按 ``collected_at`` 升序）。

    只按 ``source_id`` + ``extra_match`` 过滤，**不过滤 ``validity_state``**：
    被后续读数替代的历史行仍参与窗口。逐行解析失败（``source_extra`` 缺失、
    不可解析、缺现值、不属于本指标）只跳过该行。
    """
    current = now or datetime.now(UTC)
    since = current - timedelta(days=SERIES_RETENTION_DAYS)
    parsed: list[Observation] = []
    for signal_id, collected_at, raw_data in session.execute(
        select(RawSignal.id, RawSignal.collected_at, RawSignal.raw_data)
        .where(RawSignal.source_id == source_id, RawSignal.collected_at >= since)
        .order_by(RawSignal.collected_at, RawSignal.id)
    ):
        if not isinstance(raw_data, dict):
            continue
        extra = _normalize_extra(raw_data.get("source_extra"))
        if extra is None or not _matches(spec, extra):
            continue
        value = _number(extra.get(spec.value_key))
        if value is None:
            continue
        change = _number(extra.get(spec.change_key)) if spec.change_key else None
        parsed.append(
            Observation(
                signal_id=signal_id,
                collected_at=collected_at,
                value=value,
                change_pct=change,
            )
        )
    return tuple(parsed)


def compute_stats(spec: IndicatorSpec, series: tuple[Observation, ...]) -> WindowStats:
    """尾部窗口统计：累计变动（持续口径）与单日最大变动（冲击口径）。

    ``cum_pct = value_now / value_{t-N} - 1``，N = ``sustained_window``；观察点
    不足 N 时按可用点计算（不足 2 点则不可计算）。``max_day_change`` 取近
    ``shock_window`` 个观察点内、**方向符合指标设定**的最大单日变化（有符号），
    ``max_day_pct`` 为其绝对值。
    """
    count = len(series)
    cum_pct, cum_window, base_value = _cumulative_change(spec, series)
    max_day_change = _worst_daily_change(spec, series)
    return WindowStats(
        observations=count,
        cum_pct=cum_pct,
        cum_window=cum_window,
        base_value=base_value,
        latest_value=series[-1].value if series else None,
        max_day_change=max_day_change,
        max_day_pct=abs(max_day_change) if max_day_change is not None else None,
    )


def _cumulative_change(
    spec: IndicatorSpec, series: tuple[Observation, ...]
) -> tuple[float | None, int, float | None]:
    if len(series) < 2:
        return None, 0, None
    steps = min(spec.sustained_window, len(series) - 1)
    base = series[len(series) - 1 - steps].value
    if base == 0:
        return None, steps + 1, base
    return (series[-1].value / base - 1.0) * 100.0, steps + 1, base


def _worst_daily_change(
    spec: IndicatorSpec, series: tuple[Observation, ...]
) -> float | None:
    """近 shock_window 个观察点内、方向符合的最大单日变化（有符号）。

    单日变化优先取接口的 ``change_key``；缺失时用相邻两个观察点的现值推导
    （首点无前值，不可推导）。
    """
    changes = _daily_changes(series)[-spec.shock_window :]
    if not changes:
        return None
    if spec.direction == "up":
        upward = [value for value in changes if value > 0]
        return max(upward) if upward else None
    if spec.direction == "down":
        downward = [value for value in changes if value < 0]
        return min(downward) if downward else None
    return max(changes, key=abs)


def _daily_changes(series: tuple[Observation, ...]) -> list[float]:
    changes: list[float] = []
    for index, item in enumerate(series):
        if item.change_pct is not None:
            changes.append(item.change_pct)
            continue
        if index == 0:
            continue  # 首点无前值，单日变化不可推导
        previous = series[index - 1].value
        if previous == 0:
            continue
        changes.append((item.value / previous - 1.0) * 100.0)
    return changes


def evaluate_series(spec: IndicatorSpec, series: tuple[Observation, ...]) -> MarketVerdict:
    """OR 触发判定：持续条件 ∪ 冲击条件；阈值含等于。

    持续条件要求观察点数已达 ``min_observations``；冲击条件只看单日幅度，
    不受最小观察点数约束，因此单条带 ``change_key`` 的读数即可触发。
    """
    stats = compute_stats(spec, series)
    sustained = _sustained_magnitude(spec, stats)
    shock = _shock_magnitude(spec, stats)
    triggered = sustained is not None or shock is not None
    if not triggered:
        return MarketVerdict(
            triggered=False,
            condition=None,
            severity=None,
            magnitude_pct=None,
            trigger_change=None,
            sustained_triggered=False,
            shock_triggered=False,
            stats=stats,
        )
    # 越阈较大者决定分档与主叙述条件；两者相等时以持续条件为准（列表顺序保证确定性）。
    candidates: list[tuple[float, str]] = []
    if sustained is not None:
        candidates.append((sustained, CONDITION_SUSTAINED))
    if shock is not None:
        candidates.append((shock, CONDITION_SHOCK))
    magnitude, condition = max(candidates, key=lambda item: item[0])
    signed = stats.cum_pct if condition == CONDITION_SUSTAINED else stats.max_day_change
    return MarketVerdict(
        triggered=True,
        condition=condition,
        severity=_severity_for(spec, magnitude),
        magnitude_pct=magnitude,
        trigger_change=signed,
        sustained_triggered=sustained is not None,
        shock_triggered=shock is not None,
        stats=stats,
    )


def _sustained_magnitude(spec: IndicatorSpec, stats: WindowStats) -> float | None:
    if stats.observations < spec.min_observations or stats.cum_pct is None:
        return None
    if not _direction_ok(spec.direction, stats.cum_pct):
        return None
    magnitude = abs(stats.cum_pct)
    return magnitude if magnitude >= spec.sustained_change_pct else None


def _shock_magnitude(spec: IndicatorSpec, stats: WindowStats) -> float | None:
    # 方向已在 _worst_daily_change 中按指标设定筛选，这里只比阈值（含等于）。
    if stats.max_day_pct is None:
        return None
    return stats.max_day_pct if stats.max_day_pct >= spec.shock_change_pct else None


def _direction_ok(direction: str, value: float) -> bool:
    if direction == "up":
        return value > 0
    if direction == "down":
        return value < 0
    return True


def _severity_for(spec: IndicatorSpec, magnitude: float) -> str:
    """按幅度落档。注册表保证 severity_bands 按阈值降序排列。"""
    for band in spec.severity_bands:
        if magnitude >= band.threshold_pct:
            return band.severity
    # 越过触发线但低于最低档（运行时覆盖可能出现）：确定性回落到最低档，
    # 绝不返回空严重性。
    return spec.severity_bands[-1].severity


def build_result(
    spec: IndicatorSpec, verdict: MarketVerdict, *, start_at: datetime
) -> SignalAnalysisResult:
    """构造可直接落库的分析结果（无主体、无地点、无模型调用）。

    行情类事件只写行业词表，汇率类只写结算币种；等级留给确定性规则引擎
    （``suggested_level=None``）。摘要与证据只陈述已发生的统计事实，
    不含任何预测性表述。
    """
    if not verdict.triggered or verdict.severity is None:
        raise ValueError("未触发的行情评估不能构造分析结果")
    stats = verdict.stats
    direction_zh = _direction_zh(verdict.trigger_change)
    summary_zh = (
        f"{spec.label}（{spec.code}）出现{direction_zh}变动："
        f"近 {stats.cum_window} 个观察点累计变动 {_pct_text(stats.cum_pct)}，"
        f"持续触发线 {spec.sustained_change_pct:g}%；"
        f"近 {spec.shock_window} 个观察点内单日最大变动 "
        f"{_pct_text(stats.max_day_change)}，冲击触发线 {spec.shock_change_pct:g}%。"
    )
    evidence = [
        (
            f"统计口径：指标 {spec.code}（{spec.label}），单位 {spec.unit}，"
            f"基于最近 {SERIES_RETENTION_DAYS} 天内 {stats.observations} 个采集观察点"
            "复算，未使用模型推断。"
        ),
        (
            f"累计变动：近 {stats.cum_window} 个观察点由 {_value_text(stats.base_value)} "
            f"变为 {_value_text(stats.latest_value)}，变动 {_pct_text(stats.cum_pct)}"
            f"（持续触发线 {spec.sustained_change_pct:g}%，最小观察点数 "
            f"{spec.min_observations}，当前 {stats.observations} 个）。"
        ),
        (
            f"单日变动：近 {spec.shock_window} 个观察点内单日最大变动 "
            f"{_pct_text(stats.max_day_change)}（冲击触发线 {spec.shock_change_pct:g}%），"
            f"方向设定 {spec.direction}，实际方向 {direction_zh}。"
        ),
        (
            f"判定结果：{'持续条件' if verdict.sustained_triggered else ''}"
            f"{'与' if verdict.sustained_triggered and verdict.shock_triggered else ''}"
            f"{'冲击条件' if verdict.shock_triggered else ''}成立，"
            f"越阈幅度 {_pct_text(verdict.trigger_change)}，严重性档位 "
            f"{verdict.severity}。"
        ),
    ]
    return SignalAnalysisResult(
        # 事件分类与严重性取自注册表/分档表（运行时会再按 Literal 校验一次）。
        event_type=cast("EventType", spec.event_type),
        event_subtype=cast("EventSubtype", spec.event_subtype),
        suggested_severity=cast("Severity", verdict.severity),
        organizations=[],
        locations=[],
        affected_activities=[],
        affected_products=[],
        affected_industries=list(spec.affected_industries),
        affected_countries=[],
        affected_currencies=list(spec.affected_currencies),
        start_at=start_at,
        end_at=None,
        summary_zh=summary_zh,
        evidence_sentences=evidence,
        confidence=DETERMINISTIC_CONFIDENCE,
        suggested_level=None,
    )


def build_filtered_reason(spec: IndicatorSpec, verdict: MarketVerdict) -> FilteredReason:
    """未触发时的过滤原因：区分"没有数据 / 还在积累 / 未到阈值"。"""
    if verdict.triggered:
        raise ValueError("已触发的行情评估不需要过滤原因")
    stats = verdict.stats
    if stats.observations == 0:
        return FilteredReason(
            code=REASON_NO_OBSERVATIONS,
            detail_zh=(
                f"无可用观察点：最近 {SERIES_RETENTION_DAYS} 天内没有读到 "
                f"{spec.label}（{spec.code}）的结构化行情读数"
            ),
        )
    if stats.observations < spec.min_observations:
        return FilteredReason(
            code=REASON_ACCUMULATING,
            detail_zh=(
                f"数据积累中：{spec.label} 已有 {stats.observations}/"
                f"{spec.min_observations} 个观察点，未达持续判定所需的最小观察点数"
            ),
        )
    return FilteredReason(
        code=REASON_BELOW_THRESHOLD,
        detail_zh=(
            f"未达触发线：近 {stats.cum_window} 个观察点累计变动 "
            f"{_pct_text(stats.cum_pct)}（持续触发线 {spec.sustained_change_pct:g}%），"
            f"近 {spec.shock_window} 个观察点内单日最大变动 "
            f"{_pct_text(stats.max_day_change)}（冲击触发线 {spec.shock_change_pct:g}%）"
        ),
    )


def resolve_episode_start(
    session: Session,
    spec: IndicatorSpec,
    *,
    source_id: int,
    series: tuple[Observation, ...],
    now: datetime | None = None,
) -> datetime:
    """归并 episode：间隔在 ``episode_gap`` 内沿用上次 ``start_at``，否则开新 episode。

    间隔按「上一次触发信号与本次之间、本指标的观察点数量（含本次）」计，
    而不是自然日——采集失败当天没有读数时，自然日会误判为已断档。
    无历史记录、``start_at`` 缺失或不可解析时，一律取当前日期 UTC 00:00。
    """
    current = now or datetime.now(UTC)
    today = _day_start(current)
    if not series:
        return today
    prior = _prior_trigger(session, spec, source_id=source_id)
    if prior is None:
        return today
    prior_collected_at, prior_start_at = prior
    gap = sum(1 for item in series if item.collected_at > prior_collected_at)
    if gap <= spec.episode_gap and prior_start_at is not None:
        return prior_start_at
    return today


def _prior_trigger(
    session: Session, spec: IndicatorSpec, *, source_id: int
) -> tuple[datetime, datetime | None] | None:
    rows = session.execute(
        select(AIAnalysisRecord.result, RawSignal.collected_at, RawSignal.raw_data)
        .join(RawSignal, RawSignal.id == AIAnalysisRecord.signal_id)
        .where(
            AIAnalysisRecord.provider == DETERMINISTIC_PROVIDER,
            AIAnalysisRecord.status == "succeeded",
            AIAnalysisRecord.result.is_not(None),
            RawSignal.source_id == source_id,
        )
        .order_by(AIAnalysisRecord.id.desc())
    ).all()
    for result, collected_at, raw_data in rows:
        # 记录必须挂在同指标读数上，否则会把别的品种/币种的 episode 并进来。
        if not isinstance(raw_data, dict):
            continue
        extra = _normalize_extra(raw_data.get("source_extra"))
        if extra is None or not _matches(spec, extra):
            continue
        return collected_at, _parse_start_at(result)
    return None


def _parse_start_at(result: object) -> datetime | None:
    if not isinstance(result, dict):
        return None
    raw = result.get("start_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _day_start(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _direction_zh(signed: float | None) -> str:
    if signed is None:
        return "方向不可判定"
    return "上行" if signed > 0 else "下行" if signed < 0 else "持平"


def _pct_text(value: float | None) -> str:
    return "不可计算" if value is None else f"{value:+.2f}%"


def _value_text(value: float | None) -> str:
    return "不可计算" if value is None else f"{value:g}"