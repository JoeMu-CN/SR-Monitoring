"""只读监控健康聚合：不写任何表，只把已持久化的观测与业务事实汇总成健康视图。

口径要点：
- 心跳：``scheduler_runtime_state`` 中 ``scheduler`` 行的 ``heartbeat_at``；
  从未写入=unknown，``now - HEARTBEAT_STALE_SECONDS`` 之前=stale，否则=ok。
- 信源：与采集链同款能力判定（``build_pull_adapter``）区分拉取源与按需源；
  ``schedule=NULL`` 的可调度拉取源使用 ``SCHEDULER_COLLECT_CRON`` 推导下一预期；
  仅 manual-json / external_tool / 无法构建拉取适配器的来源为 on_demand。
- 锚点：最近成功（runtime 优先于可被 30 天清理的 collection_runs），
  无成功时用首次创建/最近配置更新时间推导首个预期触发点，再加 300 秒宽限。
- pending：复用业务候选谓词（不含 batch limit），classification_failed 另列。
- 状态判定全部在 ``health_rules``（纯函数）；本模块只做数据聚合。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import SCHEDULER_COLLECT_CRON
from app.scheduler.health_rules import (
    ACTIVE_ADAPTER_STATES,
    classify_schedule_source,
    overall_status,
)
from app.scheduler.health_schemas import (
    HeartbeatStatus,
    MonitoringHealthRead,
    ProcessingHealthRead,
    ProcessingRunRead,
    RunStatus,
    SchedulerHealthRead,
    SourceHealthRead,
    SourceHealthStatus,
)
from app.scheduler.jobs import pending_signal_candidate_id_select
from app.scheduler.runtime import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_JOB_KEY,
    HEARTBEAT_STALE_SECONDS,
    PENDING_SIGNALS_JOB_KEY,
    source_collection_job_key,
)
from app.scheduler.runtime_models import SchedulerRuntimeState
from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.router import build_pull_adapter
from app.signals.service import SourceNotCollectable


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# runtime.status 是普通 Text 列；仅接受四个稳定取值，其余按 idle 处理。
_RUN_STATUS_BY_NAME: dict[str, RunStatus] = {
    "idle": "idle",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
}


def _max_utc(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _is_pull_source(source: DataSource) -> bool:
    """与采集链同款能力判定：manual-json/external_tool 及无法构建拉取适配器的不是拉取源。"""
    if source.source_type == "external_tool" or source.code == "manual-json":
        return False
    try:
        build_pull_adapter(source)
    except SourceNotCollectable:
        return False
    return True


def _runtime_success_at(row: SchedulerRuntimeState | None) -> datetime | None:
    if row is not None and row.status == "succeeded":
        return _as_utc(row.last_success_at)
    return None


def _runtime_failure_at(row: SchedulerRuntimeState | None) -> datetime | None:
    if row is not None and row.status == "failed":
        return _as_utc(row.last_finished_at)
    return None


def _heartbeat(row: SchedulerRuntimeState | None, now: datetime) -> SchedulerHealthRead:
    last = _as_utc(row.heartbeat_at) if row is not None else None
    status: HeartbeatStatus
    if last is None:
        status = "unknown"
        age: int | None = None
    else:
        age = int((now - last).total_seconds())
        status = "ok" if age <= HEARTBEAT_STALE_SECONDS else "stale"
    return SchedulerHealthRead(
        status=status,
        last_heartbeat_at=last,
        age_seconds=age,
        interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        stale_after_seconds=HEARTBEAT_STALE_SECONDS,
    )


def _pending(
    session: Session,
    runtime: dict[str, SchedulerRuntimeState],
    now: datetime,
) -> ProcessingHealthRead:
    candidate_ids = pending_signal_candidate_id_select().subquery()
    candidate_id_column = candidate_ids.c.signal_id
    total = session.scalar(select(func.count()).select_from(candidate_ids)) or 0

    classification_failed = session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .join(DataSource, DataSource.id == RawSignal.source_id)
        .where(
            DataSource.enabled.is_(True),
            RawSignal.validity_reason["code"].astext == "classification_failed",
        )
    ) or 0

    backlog_cutoff = now - timedelta(hours=1)
    backlog = session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .where(
            RawSignal.id.in_(select(candidate_id_column)),
            RawSignal.collected_at < backlog_cutoff,
        )
    ) or 0
    oldest = session.scalar(
        select(func.min(RawSignal.collected_at)).where(
            RawSignal.id.in_(select(candidate_id_column))
        )
    )
    oldest_utc = _as_utc(oldest)
    oldest_age = int((now - oldest_utc).total_seconds()) if oldest_utc is not None else None

    collect_row = runtime.get(PENDING_SIGNALS_JOB_KEY)
    if collect_row is None:
        last_run = ProcessingRunRead(
            status="idle",
            started_at=None,
            finished_at=None,
            processed=0,
            filtered=0,
            failed=0,
        )
    else:
        status = _RUN_STATUS_BY_NAME.get(collect_row.status, "idle")
        last_run = ProcessingRunRead(
            status=status,
            started_at=_as_utc(collect_row.last_started_at),
            finished_at=_as_utc(collect_row.last_finished_at),
            processed=collect_row.processed_count,
            filtered=collect_row.filtered_count,
            failed=collect_row.failed_count,
        )
    return ProcessingHealthRead(
        total=int(total),
        classification_failed=int(classification_failed),
        backlog_over_1h=int(backlog),
        oldest_pending_age_seconds=oldest_age,
        last_run=last_run,
    )


def _source_health(
    session: Session,
    runtime: dict[str, SchedulerRuntimeState],
    now: datetime,
) -> list[SourceHealthRead]:
    sources = list(session.scalars(select(DataSource).order_by(DataSource.code)))
    run_rows = session.execute(
        select(
            CollectionRun.source_id,
            func.max(CollectionRun.started_at).filter(
                CollectionRun.status == "succeeded"
            ),
            func.max(CollectionRun.started_at),
            func.max(CollectionRun.started_at).filter(CollectionRun.status == "failed"),
        ).group_by(CollectionRun.source_id)
    ).all()
    by_source: dict[int, tuple[object, object, object]] = {
        int(row[0]): (row[1], row[2], row[3]) for row in run_rows
    }

    items: list[SourceHealthRead] = []
    for source in sources:
        state: SourceHealthStatus
        reason_code: str
        next_expected: datetime | None
        runtime_row = runtime.get(source_collection_job_key(source.id))
        run_success, run_latest, run_failed = by_source.get(
            source.id, (None, None, None)
        )
        # 最近成功：runtime 成功锚点优先，collection_runs（可被 30 天清理）作补充，
        # 两者都存在时取更近的一次（“最近成功”）。
        success_at = _max_utc(_runtime_success_at(runtime_row), _as_utc(run_success))  # type: ignore[arg-type]
        failure_at = _max_utc(_runtime_failure_at(runtime_row), _as_utc(run_failed))  # type: ignore[arg-type]
        attempt_at = _max_utc(
            _as_utc(runtime_row.last_started_at) if runtime_row is not None else None,
            _as_utc(runtime_row.last_finished_at) if runtime_row is not None else None,
            _as_utc(run_latest),  # type: ignore[arg-type]
        )

        if not source.enabled or source.adapter_status not in ACTIVE_ADAPTER_STATES:
            state, reason_code, next_expected = "disabled", "disabled", None
        else:
            # 只对可调度拉取源算到期：先按采集链同款能力判定拉取资格，
            # schedule=NULL 的拉取源使用 SCHEDULER_COLLECT_CRON；其余一律 on_demand。
            schedule: str | None = None
            if _is_pull_source(source):
                schedule = (
                    source.schedule
                    if source.schedule is not None
                    else SCHEDULER_COLLECT_CRON
                )
            if schedule is None:
                state, reason_code, next_expected = "on_demand", "on_demand", None
            else:
                # 锚点：最近成功；否则首次创建/最近配置更新时间。
                anchor = (
                    success_at
                    or _as_utc(source.updated_at)
                    or _as_utc(source.created_at)
                )
                state, reason_code, next_expected = classify_schedule_source(
                    schedule=schedule,
                    anchor=anchor,
                    latest_success_at=success_at,
                    latest_failure_at=failure_at,
                    now=now,
                )
        items.append(
            SourceHealthRead(
                source_id=source.id,
                code=source.code,
                name=source.name,
                state=state,
                reason_code=reason_code,
                last_success_at=success_at,
                last_attempt_at=attempt_at,
                next_expected_at=next_expected,
            )
        )
    return items


def build_monitoring_health(
    session: Session, *, now: datetime | None = None
) -> MonitoringHealthRead:
    moment = now or datetime.now(UTC)
    runtime = {
        row.job_key: row
        for row in session.scalars(select(SchedulerRuntimeState))
    }
    scheduler_health = _heartbeat(runtime.get(HEARTBEAT_JOB_KEY), moment)
    processing = _pending(session, runtime, moment)
    sources = _source_health(session, runtime, moment)
    return MonitoringHealthRead(
        as_of=moment,
        overall=overall_status(
            scheduler_status=scheduler_health.status,
            processing_total=processing.total,
            oldest_pending_age_seconds=processing.oldest_pending_age_seconds,
            last_run_status=processing.last_run.status,
            source_states=[item.state for item in sources],
        ),
        scheduler=scheduler_health,
        processing=processing,
        sources=sources,
    )
