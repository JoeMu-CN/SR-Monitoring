"""信号消费优先级计算（0-100，越高越先分析）。

采集入库后由调用方按本模块打分写入 ``raw_signals.priority``；消费队列按
``priority DESC, collected_at`` 取信号，配合老化任务避免低优先级信号饿死。
打分只依赖来源属性、供应商命中、高影响关键词、重点关注国家与有效期截止时间，
全部为确定性计算，不调用模型。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from app.signals.models import DataSource, RawSignal
from app.signals.relevance import (
    TIER_DEFAULT,
    TIER_LIST_SOURCE,
    FilterRules,
    _foreign_countries,
    load_filter_rules,
    load_supplier_profiles,
    supplier_name_index,
)
from app.suppliers.schemas import normalize_alias

# 分项加分与上限（与采集侧打分口径固定，便于事后解释队列顺序）。
SUPPLIER_HIT_BONUS = 20
HIGH_IMPACT_BONUS = 10
PRIORITY_COUNTRY_BONUS = 10
DEADLINE_SOON_BONUS = 10
PRIORITY_MAX = 100
DEADLINE_SOON_HOURS = 24
_UPDATE_BATCH_SIZE = 2000
_PRIORITY_STATES = ("pending_classification", "active")


def source_tier(source: DataSource, rules: FilterRules) -> int:
    """来源层级基准分：清单类信源 10，其余按 source_type 配置（默认 50）。"""
    if source.code in rules.list_sources:
        return TIER_LIST_SOURCE
    return rules.source_tiers.get(source.source_type, TIER_DEFAULT)


def compute_priority(
    signal: RawSignal,
    source: DataSource,
    *,
    supplier_hit: bool,
    rules: FilterRules,
    now_utc: datetime | None = None,
) -> int:
    """按来源层级 + 相关性线索计算消费优先级，结果封顶 100。"""
    now = now_utc or datetime.now(UTC)
    score = source_tier(source, rules)
    if supplier_hit:
        score += SUPPLIER_HIT_BONUS
    raw_text = f"{signal.title} {signal.content}"
    text = normalize_alias(raw_text)
    if any(keyword in text for keyword in rules.high_impact):
        score += HIGH_IMPACT_BONUS
    # 复用 relevance 模块的私有国家识别表（同包内部复用，避免两套地理词表漂移）：
    # 命中海外供应链重点关注国家时提权，补偿其暂无本地供应商的劣势。
    if _foreign_countries(text, raw_text) & rules.priority_countries:
        score += PRIORITY_COUNTRY_BONUS
    deadline = signal.valid_until
    if deadline is not None:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline <= now + timedelta(hours=DEADLINE_SOON_HOURS):
            score += DEADLINE_SOON_BONUS
    return min(score, PRIORITY_MAX)


def assign_signal_priorities(
    session: Session,
    signal_ids: Sequence[int],
    *,
    now_utc: datetime | None = None,
) -> int:
    """给指定信号批量计算并写入消费优先级（采集入库后调用）。

    只处理 pending_classification / active；一次 commit；异常由调用方兜底。
    供应商命中索引只加载一次，避免逐条信号全表扫描。
    """
    if not signal_ids:
        return 0
    now = now_utc or datetime.now(UTC)
    rows = session.execute(
        select(RawSignal, DataSource)
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(
            RawSignal.id.in_(list(signal_ids)),
            RawSignal.validity_state.in_(_PRIORITY_STATES),
        )
    ).all()
    if not rows:
        return 0
    rules = load_filter_rules(session)
    supplier_index = supplier_name_index(load_supplier_profiles(session))
    scored: list[dict[str, object]] = []
    for signal, source in rows:
        text = normalize_alias(f"{signal.title} {signal.content}")
        supplier_hit = any(name in text for name in supplier_index)
        scored.append(
            {
                "target_signal_id": signal.id,
                "target_priority": compute_priority(
                    signal,
                    source,
                    supplier_hit=supplier_hit,
                    rules=rules,
                    now_utc=now,
                ),
            }
        )
    statement = (
        update(RawSignal)
        .where(RawSignal.id == bindparam("target_signal_id"))
        .values(priority=bindparam("target_priority"))
    )
    connection = session.connection()
    for start in range(0, len(scored), _UPDATE_BATCH_SIZE):
        connection.execute(statement, scored[start : start + _UPDATE_BATCH_SIZE])
    session.commit()
    return len(scored)
