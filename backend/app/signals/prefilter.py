"""采集入队时的确定性预筛选。

目标：把「无需 LLM」的信号在入库后立即判定并移出消费队列，避免清单类/灾害类
信号把队列堵死。判定口径与 ``app.scheduler.jobs`` 消费链内的逐条过滤完全一致
（相关性 → 清单类未命中供应商 → 结构化分级），只是提前到采集事务提交后批量执行。

行情指标评估（market_indicator）仍留在消费链，因为需要读取本轮采集的读数序列。

失败处理：预筛是优化而非采集的一部分，任何异常都只记 warning 并回滚，绝不阻断采集。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import bindparam, insert, select, update
from sqlalchemy.orm import Session

from app.ai.models import AIAnalysisRecord
from app.signals.models import DataSource, RawSignal
from app.signals.relevance import (
    FilterRules,
    SupplierProfile,
    assess_text_relevance,
    grade_structured_signal,
    load_filter_rules,
    load_supplier_profiles,
    supplier_name_index,
)
from app.signals.validity import ValidityState
from app.suppliers.schemas import normalize_alias

logger = logging.getLogger(__name__)

# 三类确定性过滤器的 filter_kind 取值，与 app.scheduler.jobs 的同名常量逐字一致：
# 消费链仍在做同样的判定（开关关闭或历史积压时），两侧写入的 details.filter 必须相同。
RELEVANCE_FILTER_KIND: Final = "relevance"
ENTITY_LIST_FILTER_KIND: Final = "entity_list"
STRUCTURED_GRADE_FILTER_KIND: Final = "structured_grade"

# 过滤记录（AIAnalysisRecord）与信号状态更新的批大小。
_RECORD_BATCH_SIZE: Final = 1000
_UPDATE_BATCH_SIZE: Final = 2000

# 只有尚未定态的信号参与预筛：已过期/已替代/已撤销的信号不再改写。
_PREFILTER_STATES: Final = (
    ValidityState.PENDING_CLASSIFICATION.value,
    ValidityState.ACTIVE.value,
)


@dataclass(frozen=True)
class PrefilterOutcome:
    """一轮预筛结果：filtered=移出队列数，kept=仍需 LLM 分析数，kept_ids=保留信号 id。"""

    filtered: int
    kept: int
    kept_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class _FilterHit:
    """单条信号的过滤结论（写记录与更新状态共用）。"""

    signal_id: int
    kind: str
    reason: str
    model: str
    error: str
    review_reason: str
    anchor_source: str


def prefilter_new_signals(
    session: Session,
    signal_ids: Sequence[int],
    *,
    now_utc: datetime | None = None,
) -> PrefilterOutcome:
    """批量预筛刚入库的信号；只 commit 一次，异常时回滚并返回 (0, 0)。

    调用方须在采集事务提交后调用：本函数会 commit，不能与采集写入共用事务，
    否则预筛失败回滚会连带丢失新信号。
    """
    if not signal_ids:
        return PrefilterOutcome(filtered=0, kept=0)
    now = now_utc or datetime.now(UTC)
    try:
        rows = session.execute(
            select(RawSignal, DataSource)
            .join(DataSource, DataSource.id == RawSignal.source_id)
            .where(
                RawSignal.id.in_(list(signal_ids)),
                RawSignal.validity_state.in_(_PREFILTER_STATES),
            )
        ).all()
        if not rows:
            return PrefilterOutcome(filtered=0, kept=0)
        rules = load_filter_rules(session)
        profiles = load_supplier_profiles(session)
        supplier_index = supplier_name_index(profiles)
        hits: list[_FilterHit] = []
        kept_ids: list[int] = []
        for signal, source in rows:
            hit = _classify(
                signal,
                source,
                rules=rules,
                profiles=profiles,
                supplier_index=supplier_index,
            )
            if hit is not None:
                hits.append(hit)
            else:
                kept_ids.append(signal.id)
        if hits:
            _write_filter_records(session, hits, now=now)
            _mark_irrelevant(session, hits)
            session.commit()
        return PrefilterOutcome(
            filtered=len(hits), kept=len(kept_ids), kept_ids=tuple(kept_ids)
        )
    except Exception as exc:  # noqa: BLE001 —— 预筛失败绝不能阻断采集
        session.rollback()
        logger.warning("采集后确定性预筛失败，本轮跳过（信号保持待分析）: %s", exc)
        return PrefilterOutcome(filtered=0, kept=0)


def _classify(
    signal: RawSignal,
    source: DataSource,
    *,
    rules: FilterRules,
    profiles: Sequence[SupplierProfile],
    supplier_index: frozenset[str],
) -> _FilterHit | None:
    """按消费链顺序判定：相关性 → 清单类 → 结构化分级；不需要过滤返回 None。"""
    # 行情指标信号带 source_extra 读数，必须留给消费链的确定性评估：
    # 预筛的相关性判定可能把「尚未触发阈值的行情读数」误判为无关，
    # 从而丢掉本应由 market-eval 触发的行情事件。
    if (
        isinstance(signal.raw_data, dict)
        and signal.raw_data.get("source_extra") is not None
    ):
        return None
    decision = assess_text_relevance(
        signal.title, signal.content, rules=rules, suppliers=profiles
    )
    if not decision.relevant:
        return _FilterHit(
            signal_id=signal.id,
            kind=RELEVANCE_FILTER_KIND,
            reason=decision.reason,
            model="relevance-v1",
            error=f"filtered: {decision.reason}",
            review_reason=f"预过滤结果待复核：{decision.reason}",
            anchor_source=_anchor_source(signal),
        )
    # 清单类信源（全量实体清单）：未命中任何供应商名则跳过 LLM。
    if source.code in rules.list_sources and not _matches_supplier_index(
        f"{signal.title} {signal.content}", supplier_index
    ):
        reason = "清单类信号未命中任何供应商"
        return _FilterHit(
            signal_id=signal.id,
            kind=ENTITY_LIST_FILTER_KIND,
            reason=reason,
            model="entity-list-v1",
            error=f"filtered: {reason}",
            review_reason="实体清单未命中任何供应商，跳过 LLM",
            anchor_source=_anchor_source(signal),
        )
    # 结构化宏观信号分级：commodity 涨跌幅低于阈值 → 免 LLM。
    grade_reason = grade_structured_signal(
        source.code, signal.title, signal.content, rules
    )
    if grade_reason is not None:
        return _FilterHit(
            signal_id=signal.id,
            kind=STRUCTURED_GRADE_FILTER_KIND,
            reason=grade_reason,
            model="structured-grade-v1",
            error=f"filtered: {grade_reason}",
            review_reason=f"结构化分级跳过：{grade_reason}",
            anchor_source=_anchor_source(signal),
        )
    return None


def _matches_supplier_index(text: str, supplier_index: frozenset[str]) -> bool:
    """供应商主体/别名是否命中文本（与 jobs._matches_any_supplier 同款匹配）。"""
    normalized = normalize_alias(text)
    return any(name in normalized for name in supplier_index)


def _anchor_source(signal: RawSignal) -> str:
    """与 jobs._mark_signal_irrelevant 一致：有发布时间用 published_at 作锚点。"""
    return "published_at" if signal.published_at is not None else "collected_at"


def _write_filter_records(
    session: Session, hits: Sequence[_FilterHit], *, now: datetime
) -> None:
    """批量写入确定性过滤记录（provider=deterministic-filter，结果为空、待复核）。"""
    rows = [
        {
            "signal_id": hit.signal_id,
            "provider": "deterministic-filter",
            "model": hit.model,
            "prompt_version": hit.model,
            "status": "succeeded",
            "finished_at": now,
            "duration_ms": 0,
            "result": None,
            "error": hit.error,
            "needs_review": True,
            "review_reason": hit.review_reason,
        }
        for hit in hits
    ]
    for start in range(0, len(rows), _RECORD_BATCH_SIZE):
        session.execute(insert(AIAnalysisRecord).values(rows[start : start + _RECORD_BATCH_SIZE]))


def _mark_irrelevant(session: Session, hits: Sequence[_FilterHit]) -> None:
    """批量把被过滤信号置为显式终态 irrelevant（不复用 ORM 对象，避免逐条 UPDATE）。"""
    reason_type = RawSignal.__table__.c.validity_reason.type
    statement = (
        update(RawSignal)
        .where(RawSignal.id == bindparam("target_signal_id"))
        .values(
            validity_state=ValidityState.IRRELEVANT,
            validity_reason=bindparam("target_reason", type_=reason_type),
        )
    )
    # 走 Core 连接执行 executemany：避免 ORM 的「按主键批量 UPDATE」语义要求参数带 id。
    connection = session.connection()
    for start in range(0, len(hits), _UPDATE_BATCH_SIZE):
        batch = hits[start : start + _UPDATE_BATCH_SIZE]
        connection.execute(
            statement,
            [
                {
                    "target_signal_id": hit.signal_id,
                    "target_reason": {
                        "code": "filtered_out",
                        "anchor_source": hit.anchor_source,
                        "details": {"filter": hit.kind, "reason": hit.reason},
                    },
                }
                for hit in batch
            ],
        )
