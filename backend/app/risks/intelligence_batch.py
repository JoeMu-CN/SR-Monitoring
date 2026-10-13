"""情报报告的批量生成（Scheduler 入口）：挑选待生成事件并逐条调用生成服务。

与 ``intelligence_service`` 的分工：后者只负责「一条事件 → 一份报告」，本模块只
负责「哪些事件还没报告」与「一批跑完不要互相踩」。两者都不碰提醒链路。

待生成判定（三层，缺一不可）：
1. 事件至少有一条关联信号，其信源被 policy / industry 维度声明（信源归属）。
2. 该事件还没有 status='ready' 的报告（已成功过就不重复消耗模型额度）。
3. 若上一次生成是 failed，距今必须超过 ``INTELLIGENCE_RETRY_COOLDOWN_HOURS``。

第 1、2 条可在 SQL 里粗筛（按信源 code 与 ready 子查询），第 1 条的「同一事件
全部信号必须同分类」与第 3 条需要 Python 判定，因此粗筛结果要乘以扫描倍数再
截断到批大小。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.ai.intelligence_providers import build_intelligence_provider
from app.ai.intelligence_schemas import IntelligenceCategory
from app.config import (
    INTELLIGENCE_REPORT_BATCH,
    INTELLIGENCE_RETRY_COOLDOWN_HOURS,
    get_ai_settings,
)
from app.database import SessionLocal
from app.risks.intelligence_models import IntelligenceReport
from app.risks.intelligence_service import (
    generate_intelligence_report,
    intelligence_source_categories,
    resolve_intelligence_category,
)
from app.risks.models import RiskEvent, RiskEventSignal
from app.signals.models import DataSource, RawSignal

logger = logging.getLogger("risks.intelligence")

#: SQL 粗筛行里可能大部分被冷却期或归属冲突挡掉，因此放宽扫描倍数再截断到批大小。
_SCAN_FACTOR = 5

# 当前部署为单 Scheduler 进程；扩为多副本前需改用数据库级领取或 advisory lock。
_batch_lock = Lock()


def _candidate_event_select() -> Select[tuple[int, RiskEvent]]:
    """情报信源上「尚无 ready 报告」的事件，按事件 id 去重。"""
    ready_event_ids = select(IntelligenceReport.event_id).where(
        IntelligenceReport.status == "ready"
    )
    return (
        select(RiskEventSignal.event_id, RiskEvent)
        .join(RiskEvent, RiskEvent.id == RiskEventSignal.event_id)
        .join(RawSignal, RawSignal.id == RiskEventSignal.signal_id)
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(
            DataSource.code.in_(tuple(intelligence_source_categories())),
            RiskEvent.id.not_in(ready_event_ids),
        )
        .order_by(RiskEvent.id.desc())
        .limit(INTELLIGENCE_REPORT_BATCH * _SCAN_FACTOR)
    )


def _failed_report_times(session: Session, event_ids: list[int]) -> dict[int, datetime]:
    if not event_ids:
        return {}
    rows = session.execute(
        select(IntelligenceReport.event_id, IntelligenceReport.updated_at).where(
            IntelligenceReport.event_id.in_(event_ids),
            IntelligenceReport.status == "failed",
        )
    ).all()
    return {
        event_id: updated_at for event_id, updated_at in rows if updated_at is not None
    }


def _event_source_codes(session: Session, event_ids: list[int]) -> dict[int, set[str]]:
    """事件的全部关联信源 code；代表信号之外的信源同样参与归属判定。"""
    rows = session.execute(
        select(RiskEventSignal.event_id, DataSource.code)
        .join(RawSignal, RawSignal.id == RiskEventSignal.signal_id)
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(RiskEventSignal.event_id.in_(event_ids))
    ).all()
    codes: dict[int, set[str]] = {}
    for event_id, code in rows:
        codes.setdefault(event_id, set()).add(code)
    return codes


def pending_intelligence_events(
    session: Session,
    *,
    now_utc: datetime | None = None,
    limit: int | None = None,
) -> list[tuple[RiskEvent, IntelligenceCategory]]:
    """待生成的 (事件, 分类)；已 ready 与冷却期内的 failed 事件都被排除。"""
    now = now_utc or datetime.now(UTC)
    batch = INTELLIGENCE_REPORT_BATCH if limit is None else limit
    events: dict[int, RiskEvent] = {}
    for event_id, event in session.execute(_candidate_event_select()).all():
        events.setdefault(event_id, event)
    if not events:
        return []

    codes_by_event = _event_source_codes(session, list(events))
    failed_at = _failed_report_times(session, list(events))
    cooldown = timedelta(hours=INTELLIGENCE_RETRY_COOLDOWN_HOURS)

    pending: list[tuple[RiskEvent, IntelligenceCategory]] = []
    for event_id, event in events.items():
        category = resolve_intelligence_category(codes_by_event.get(event_id, set()))
        if category is None:
            continue
        last_failure = failed_at.get(event_id)
        if last_failure is not None and last_failure > now - cooldown:
            continue
        pending.append((event, category))
        if len(pending) == batch:
            break
    return pending


def generate_pending_intelligence_reports(
    *, limit: int | None = None, now_utc: datetime | None = None
) -> int:
    """为待生成的政策/行业事件批量生成情报报告，返回成功条数。"""
    if not _batch_lock.acquire(blocking=False):
        logger.info("已有情报报告批次运行，跳过本次重复生成")
        return 0
    try:
        with SessionLocal() as session:
            targets = pending_intelligence_events(
                session, limit=limit, now_utc=now_utc
            )
            if not targets:
                logger.info("没有待生成的政策/行业情报报告")
                return 0
            provider = build_intelligence_provider(get_ai_settings())
            generated = 0
            for event, category in targets:
                try:
                    report = asyncio.run(
                        generate_intelligence_report(
                            session, event, category, provider=provider
                        )
                    )
                except Exception as exc:
                    # 单条失败不终止整批：会话已不可用，先回滚再继续下一条。
                    session.rollback()
                    logger.exception(
                        "情报报告生成异常: event=%s category=%s: %s", event.id, category, exc
                    )
                    continue
                if report is not None and report.status == "ready":
                    generated += 1
        logger.info("本次生成情报报告 %d/%d 条", generated, len(targets))
        return generated
    finally:
        _batch_lock.release()