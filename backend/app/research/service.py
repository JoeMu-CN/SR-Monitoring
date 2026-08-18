"""研究任务的状态、租约和幂等治理。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.research.models import (
    ResearchBatch,
    ResearchProviderQuotaPeriod,
    ResearchReport,
    ResearchSource,
    ResearchTask,
    ResearchTaskEvent,
    ResearchToolRun,
    ResearchWorkerHeartbeat,
)
from app.research.reporting import (
    REQUIRED_DISCLAIMER,
    ResearchCitationDraft,
    ResearchClaimDraft,
    ResearchReportDraft,
    validate_report_draft,
)
from app.suppliers.models import Supplier

TASK_ADMIN_ROLES = {"risk_admin", "platform_admin"}
DEFAULT_RESEARCH_BUDGET: dict[str, object] = {
    "max_queries": 5,
    "max_results": 50,
    "max_pages": 20,
    "max_input_tokens": 20_000,
    "max_output_tokens": 5_000,
}
DEFAULT_MONTHLY_RESEARCH_BUDGET: dict[str, object] = {
    **DEFAULT_RESEARCH_BUDGET,
    "max_queries": 3,
}
LEASE_RENEWAL_SECONDS = 300


class ResearchBudgetExceeded(RuntimeError):
    """研究任务预计用量超过预算快照。"""


class ResearchTaskStartError(ValueError):
    """研究任务不满足受控读取的显式启动条件。"""


class ResearchTaskDeleteError(ValueError):
    """研究任务当前不允许删除。"""


class ResearchTaskSkipped(RuntimeError):
    """研究任务在执行前因业务前置条件不满足而跳过。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ResearchProviderQuotaExceeded(RuntimeError):
    """Provider 自然月额度不足，拒绝发起外部搜索。"""


def _extend_active_task_lease(task: ResearchTask, current: datetime) -> None:
    """在有效进度写入点顺延租约；不复活已经过期的任务。"""
    if task.lease_until is not None and task.lease_until > current:
        task.lease_until = max(
            task.lease_until,
            current + timedelta(seconds=LEASE_RENEWAL_SECONDS),
        )


@dataclass(frozen=True)
class ProviderQuotaReservation:
    """一次已持久化的 Provider 搜索额度预占。"""

    provider: str
    period_key: str
    task_kind: str
    units: int

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "period_key": self.period_key,
            "task_kind": self.task_kind,
            "units": self.units,
            "settled": False,
        }


def reserve_provider_quota(
    session: Session,
    *,
    provider: str,
    task_type: str,
    monthly_limit: int,
    units: int = 1,
    now: datetime | None = None,
) -> ProviderQuotaReservation | None:
    """在 Provider 自然月账本中原子预占额度；Fake Provider 不计入。"""
    normalized_provider = provider.strip().lower()
    if normalized_provider in {"", "fake", "none"}:
        return None
    if units < 1 or monthly_limit < 1:
        raise ValueError("Provider 额度参数必须大于 0")
    if task_type not in {"manual", "daily", "weekly", "monthly"}:
        raise ValueError("不支持的研究任务类型")
    period_key = (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m"
    )
    task_kind = "manual" if task_type == "manual" else "scheduled"
    session.execute(
        pg_insert(ResearchProviderQuotaPeriod)
        .values(
            provider=normalized_provider,
            period_key=period_key,
            monthly_limit=monthly_limit,
        )
        .on_conflict_do_nothing(index_elements=["provider", "period_key"])
    )
    period = session.scalar(
        select(ResearchProviderQuotaPeriod)
        .where(
            ResearchProviderQuotaPeriod.provider == normalized_provider,
            ResearchProviderQuotaPeriod.period_key == period_key,
        )
        .with_for_update()
    )
    if period is None:
        raise RuntimeError("Provider 额度账本初始化失败")
    projected = period.used + period.scheduled_reserved + period.manual_reserved + units
    if projected > period.monthly_limit:
        raise ResearchProviderQuotaExceeded(
            f"Provider {normalized_provider} 已达到 {period_key} 月度额度"
        )
    if task_kind == "scheduled":
        period.scheduled_reserved += units
    else:
        period.manual_reserved += units
    session.commit()
    return ProviderQuotaReservation(
        provider=normalized_provider,
        period_key=period_key,
        task_kind=task_kind,
        units=units,
    )


def settle_provider_quota(
    session: Session,
    *,
    reservation: dict[str, object],
    count_as_used: bool,
) -> None:
    """结算一次预占；重复结算由 settled 标记幂等跳过。"""
    if reservation.get("settled") is True:
        return
    provider = reservation.get("provider")
    period_key = reservation.get("period_key")
    task_kind = reservation.get("task_kind")
    units = reservation.get("units")
    if (
        not isinstance(provider, str)
        or not isinstance(period_key, str)
        or task_kind not in {"manual", "scheduled"}
        or not isinstance(units, int)
        or units < 1
    ):
        raise ValueError("Provider 额度预占记录无效")
    period = session.scalar(
        select(ResearchProviderQuotaPeriod)
        .where(
            ResearchProviderQuotaPeriod.provider == provider,
            ResearchProviderQuotaPeriod.period_key == period_key,
        )
        .with_for_update()
    )
    if period is None:
        raise RuntimeError("Provider 额度预占记录不存在")
    if task_kind == "scheduled":
        if period.scheduled_reserved < units:
            raise RuntimeError("Provider 定时额度预占余额不足")
        period.scheduled_reserved -= units
    else:
        if period.manual_reserved < units:
            raise RuntimeError("Provider 手动额度预占余额不足")
        period.manual_reserved -= units
    if count_as_used:
        period.used += units
    reservation.update(
        {
            "settled": True,
            "counted_as_used": count_as_used,
        }
    )


@dataclass(frozen=True)
class ToolRunClaim:
    """工具账本认领结果；只有新建记录的调用方可以执行外部动作。"""

    tool_run: ResearchToolRun
    claimed: bool


def build_tool_action_id(action_type: str, arguments: dict[str, object]) -> tuple[str, str]:
    """根据脱敏、可序列化参数建立同一任务内稳定的工具动作标识。"""
    normalized_type = action_type.strip()
    if normalized_type not in {"web_search", "public_page_read", "report_generation"}:
        raise ValueError("不支持的研究工具动作类型")
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("研究工具动作参数必须可安全序列化") from exc
    arguments_hash = sha256(encoded).hexdigest()
    return f"{normalized_type}:{arguments_hash[:48]}", arguments_hash


def claim_tool_run(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    action_type: str,
    arguments: dict[str, object],
    now: datetime | None = None,
) -> ToolRunClaim | None:
    """认领一个工具动作；同一动作只允许第一个有效租约执行。"""
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    _extend_active_task_lease(task, current)
    action_id, arguments_hash = build_tool_action_id(action_type, arguments)
    existing = session.scalar(
        select(ResearchToolRun).where(
            ResearchToolRun.task_id == task.id,
            ResearchToolRun.action_id == action_id,
        )
    )
    if existing is not None:
        if existing.arguments_hash != arguments_hash:
            raise RuntimeError("研究工具动作标识与参数不一致")
        return ToolRunClaim(tool_run=existing, claimed=False)

    tool_run = ResearchToolRun(
        task_id=task.id,
        action_id=action_id,
        action_type=action_type,
        arguments_hash=arguments_hash,
        status="running",
    )
    session.add(tool_run)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(ResearchToolRun).where(
                ResearchToolRun.task_id == task_id,
                ResearchToolRun.action_id == action_id,
            )
        )
        if existing is not None:
            return ToolRunClaim(tool_run=existing, claimed=False)
        raise
    return ToolRunClaim(tool_run=tool_run, claimed=True)


def complete_tool_run(
    session: Session,
    *,
    tool_run_id: int,
    task_id: int,
    worker_id: str,
    usage_snapshot: dict[str, object] | None = None,
    result_reference: dict[str, object] | None = None,
    now: datetime | None = None,
) -> ResearchToolRun | None:
    """将当前有效租约拥有的工具动作标为成功；账本不保存工具正文。"""
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    tool_run = session.scalar(
        select(ResearchToolRun).where(
            ResearchToolRun.id == tool_run_id,
            ResearchToolRun.task_id == task.id,
        )
    )
    if tool_run is None:
        return None
    if tool_run.status == "succeeded":
        return tool_run
    if tool_run.status != "running":
        return None
    merged_usage = dict(tool_run.usage_snapshot)
    merged_usage.update(usage_snapshot or {})
    quota = merged_usage.get("provider_quota")
    if isinstance(quota, dict):
        settle_provider_quota(
            session,
            reservation=quota,
            count_as_used=True,
        )
    tool_run.status = "succeeded"
    tool_run.usage_snapshot = merged_usage
    tool_run.result_reference = dict(result_reference or {})
    tool_run.error_category = None
    tool_run.finished_at = current
    session.commit()
    session.refresh(tool_run)
    return tool_run


def fail_tool_run(
    session: Session,
    *,
    tool_run_id: int,
    task_id: int,
    worker_id: str,
    error_category: str,
    now: datetime | None = None,
) -> ResearchToolRun | None:
    """记录不含原始异常和敏感内容的工具失败分类。"""
    normalized_category = error_category.strip().lower()
    if not normalized_category or len(normalized_category) > 100:
        raise ValueError("研究工具失败分类无效")
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    tool_run = session.scalar(
        select(ResearchToolRun).where(
            ResearchToolRun.id == tool_run_id,
            ResearchToolRun.task_id == task.id,
        )
    )
    if tool_run is None or tool_run.status != "running":
        return None
    merged_usage = dict(tool_run.usage_snapshot)
    quota = merged_usage.get("provider_quota")
    if isinstance(quota, dict):
        settle_provider_quota(
            session,
            reservation=quota,
            count_as_used=True,
        )
    tool_run.status = "failed"
    tool_run.usage_snapshot = merged_usage
    tool_run.error_category = normalized_category
    tool_run.finished_at = current
    session.commit()
    session.refresh(tool_run)
    return tool_run


def reconcile_stale_tool_runs(
    session: Session,
    *,
    stale_before: datetime,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """保守结束租约失效的遗留工具调用，并结算其 Provider 预占。

    外部请求是否已经离开进程无法从数据库可靠推断，因此对带有 Provider
    预占的动作按已使用处理；不带预占的页面读取/报告动作也必须结束，避免
    幂等账本永久停留在 ``running`` 并阻塞后续恢复。
    """
    current = now or datetime.now(UTC)
    if stale_before.tzinfo is None or current.tzinfo is None:
        raise ValueError("遗留工具调用对账时间必须带时区")
    if stale_before > current:
        raise ValueError("遗留工具调用恢复阈值不能晚于当前时间")
    if limit < 1:
        raise ValueError("遗留工具调用对账批量必须大于 0")

    stale_runs = list(
        session.scalars(
            select(ResearchToolRun)
            .join(ResearchTask, ResearchTask.id == ResearchToolRun.task_id)
            .where(
                ResearchToolRun.status == "running",
                ResearchToolRun.started_at < stale_before,
                or_(
                    ResearchTask.status != "running",
                    ResearchTask.lease_until.is_(None),
                    ResearchTask.lease_until <= current,
                ),
            )
            .order_by(ResearchToolRun.started_at, ResearchToolRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    reconciled = 0
    for tool_run in stale_runs:
        merged_usage = dict(tool_run.usage_snapshot)
        quota = merged_usage.get("provider_quota")
        quota_counted = False
        if isinstance(quota, dict):
            settle_provider_quota(
                session,
                reservation=quota,
                count_as_used=True,
            )
            quota_counted = True
        merged_usage.update(
            {
                "reconciled": True,
                "reconciled_at": current.isoformat(),
                "reconciliation_reason": "worker_lease_expired",
            }
        )
        tool_run.status = "failed"
        tool_run.usage_snapshot = merged_usage
        tool_run.error_category = "stale_reconciled"
        tool_run.finished_at = current
        append_task_event(
            session,
            task_id=tool_run.task_id,
            event_type="tool_run_reconciled",
            node_key=f"tool_run:{tool_run.id}",
            parent_node_key="recovery",
            status="failed",
            label="遗留工具调用已完成保守对账",
            detail={
                "tool_run_id": tool_run.id,
                "action_type": tool_run.action_type,
                "provider_quota_counted": quota_counted,
            },
            once=True,
        )
        reconciled += 1
    if reconciled:
        session.commit()
    return reconciled


def append_task_event(
    session: Session,
    *,
    task_id: int,
    event_type: str,
    node_key: str,
    status: str,
    label: str,
    parent_node_key: str | None = None,
    detail: dict[str, object] | None = None,
    once: bool = False,
) -> ResearchTaskEvent:
    """追加一条脱敏任务事件；由调用方随业务状态一起提交。"""
    if once:
        existing = session.scalar(
            select(ResearchTaskEvent).where(
                ResearchTaskEvent.task_id == task_id,
                ResearchTaskEvent.event_type == event_type,
                ResearchTaskEvent.node_key == node_key,
            )
        )
        if existing is not None:
            return existing
    event = ResearchTaskEvent(
        task_id=task_id,
        event_type=event_type.strip()[:100],
        node_key=node_key.strip()[:200],
        parent_node_key=(parent_node_key.strip()[:200] if parent_node_key else None),
        status=status,
        label=label.strip()[:200],
        detail=dict(detail or {}),
    )
    session.add(event)
    session.flush()
    return event


def upsert_worker_heartbeat(
    session: Session,
    *,
    worker_id: str,
    mode: str,
    orchestrator: str,
    now: datetime | None = None,
) -> ResearchWorkerHeartbeat:
    """注册或重新启动一个 Worker；只保存运行元数据，不保存任务正文。"""
    normalized_worker_id = worker_id.strip()
    normalized_mode = mode.strip()
    normalized_orchestrator = orchestrator.strip().lower()
    if not normalized_worker_id or len(normalized_worker_id) > 200:
        raise ValueError("Worker 标识无效")
    if not normalized_mode or len(normalized_mode) > 100:
        raise ValueError("Worker 模式无效")
    if normalized_orchestrator not in {"legacy", "langgraph"}:
        raise ValueError("Worker 编排器无效")
    current = now or datetime.now(UTC)
    heartbeat = session.scalar(
        select(ResearchWorkerHeartbeat)
        .where(ResearchWorkerHeartbeat.worker_id == normalized_worker_id)
        .with_for_update()
    )
    if heartbeat is None:
        heartbeat = ResearchWorkerHeartbeat(
            worker_id=normalized_worker_id,
            mode=normalized_mode,
            orchestrator=normalized_orchestrator,
            status="online",
            started_at=current,
            last_seen_at=current,
        )
        session.add(heartbeat)
    else:
        heartbeat.mode = normalized_mode
        heartbeat.orchestrator = normalized_orchestrator
        heartbeat.status = "online"
        heartbeat.started_at = current
        heartbeat.last_seen_at = current
        heartbeat.stopped_at = None
    session.commit()
    session.refresh(heartbeat)
    return heartbeat


def touch_worker_heartbeat(
    session: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> ResearchWorkerHeartbeat | None:
    """顺延现有 Worker 的心跳；找不到记录时不静默创建新 Worker。"""
    current = now or datetime.now(UTC)
    heartbeat = session.scalar(
        select(ResearchWorkerHeartbeat)
        .where(ResearchWorkerHeartbeat.worker_id == worker_id)
        .with_for_update()
    )
    if heartbeat is None:
        return None
    heartbeat.status = "online"
    heartbeat.last_seen_at = current
    heartbeat.stopped_at = None
    session.commit()
    session.refresh(heartbeat)
    return heartbeat


def stop_worker_heartbeat(
    session: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> ResearchWorkerHeartbeat | None:
    """优雅停止 Worker；异常退出则由 API 按 last_seen_at 判定 stale。"""
    current = now or datetime.now(UTC)
    heartbeat = session.scalar(
        select(ResearchWorkerHeartbeat)
        .where(ResearchWorkerHeartbeat.worker_id == worker_id)
        .with_for_update()
    )
    if heartbeat is None:
        return None
    heartbeat.status = "stopped"
    heartbeat.last_seen_at = current
    heartbeat.stopped_at = current
    session.commit()
    session.refresh(heartbeat)
    return heartbeat


def list_worker_heartbeats(
    session: Session, *, limit: int = 50
) -> list[ResearchWorkerHeartbeat]:
    """返回最近的 Worker 运行记录；调用方负责按阈值计算 online/stale。"""
    if limit < 1 or limit > 200:
        raise ValueError("Worker 状态查询数量超出范围")
    return list(
        session.scalars(
            select(ResearchWorkerHeartbeat)
            .order_by(
                ResearchWorkerHeartbeat.last_seen_at.desc(),
                ResearchWorkerHeartbeat.id.desc(),
            )
            .limit(limit)
        )
    )


def create_task(
    session: Session,
    *,
    owner_user_id: int,
    task_type: str,
    topic: str,
    supplier_scope: list[int],
    source_urls: list[str] | None = None,
    idempotency_key: str | None,
    budget_snapshot: dict[str, object] | None = None,
    batch_id: int | None = None,
) -> ResearchTask:
    """创建任务；相同用户和幂等键重复提交返回原任务。"""
    if idempotency_key:
        existing = session.scalar(
            select(ResearchTask).where(
                ResearchTask.owner_user_id == owner_user_id,
                ResearchTask.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
    task = ResearchTask(
        owner_user_id=owner_user_id,
        batch_id=batch_id,
        task_type=task_type,
        topic=topic.strip(),
        supplier_scope=sorted(set(supplier_scope)),
        source_urls=list(source_urls or []),
        budget_snapshot=dict(budget_snapshot or DEFAULT_RESEARCH_BUDGET),
        idempotency_key=idempotency_key,
        execution_requested_at=(
            datetime.now(UTC) if task_type in {"monthly", "weekly"} else None
        ),
        current_step=(
            "awaiting_research_worker" if task_type in {"monthly", "weekly"} else None
        ),
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            existing = session.scalar(
                select(ResearchTask).where(
                    ResearchTask.owner_user_id == owner_user_id,
                    ResearchTask.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
        raise
    session.refresh(task)
    return task


def ensure_periodic_task_supplier_enabled(session: Session, task: ResearchTask) -> None:
    """确认周期单供应商任务仍指向启用供应商，失败时禁止外部调用。"""
    if task.batch_id is None or task.task_type not in {"monthly", "weekly"}:
        return
    if len(task.supplier_scope) != 1:
        raise ResearchTaskSkipped("periodic_supplier_scope_invalid")
    supplier_id = task.supplier_scope[0]
    if not isinstance(supplier_id, int):
        raise ResearchTaskSkipped("periodic_supplier_id_invalid")
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise ResearchTaskSkipped("supplier_missing")
    if not supplier.enabled:
        raise ResearchTaskSkipped("supplier_disabled")


def list_tasks(
    session: Session, *, owner_user_id: int, role: str
) -> list[ResearchTask]:
    stmt = select(ResearchTask).order_by(ResearchTask.created_at.desc())
    if role not in TASK_ADMIN_ROLES:
        stmt = stmt.where(ResearchTask.owner_user_id == owner_user_id)
    return list(session.scalars(stmt))


def get_task(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> ResearchTask | None:
    task = session.get(ResearchTask, task_id)
    if task is None:
        return None
    if role not in TASK_ADMIN_ROLES and task.owner_user_id != owner_user_id:
        return None
    return task


def list_task_events(
    session: Session,
    *,
    task_id: int,
    owner_user_id: int,
    role: str,
    after_id: int,
    limit: int,
) -> list[ResearchTaskEvent] | None:
    """按事件 ID 增量读取，并沿用任务所有权隔离。"""
    if get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role) is None:
        return None
    return list(
        session.scalars(
            select(ResearchTaskEvent)
            .where(
                ResearchTaskEvent.task_id == task_id,
                ResearchTaskEvent.id > after_id,
            )
            .order_by(ResearchTaskEvent.id)
            .limit(limit)
        )
    )


def cancel_task(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> ResearchTask | None:
    task = get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role)
    if task is None:
        return None
    if task.status in {
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
        "budget_exhausted",
    }:
        return task
    now = datetime.now(UTC)
    if task.status == "queued":
        task.status = "cancelled"
        task.finished_at = now
    elif task.cancel_requested_at is None:
        task.cancel_requested_at = now
    session.commit()
    session.refresh(task)
    return task


def delete_task(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> ResearchTask | None:
    """删除一个非活动研究任务，并依靠外键级联清理其研究产物。"""
    task = session.scalar(
        select(ResearchTask)
        .where(ResearchTask.id == task_id)
        .with_for_update()
    )
    if task is None:
        return None
    if role not in TASK_ADMIN_ROLES and task.owner_user_id != owner_user_id:
        return None
    if task.status == "running":
        raise ResearchTaskDeleteError("运行中的研究任务不能删除，请先取消任务")
    session.delete(task)
    session.flush()
    return task


def cancel_batch(
    session: Session, *, batch_id: int, owner_user_id: int, role: str
) -> ResearchBatch | None:
    """取消周期批次；运行中子任务只请求取消以保持 Worker 容量占用。"""
    batch = get_batch(session, batch_id=batch_id, owner_user_id=owner_user_id, role=role)
    if batch is None:
        return None
    if batch.status in {"succeeded", "partial", "failed", "cancelled", "budget_exhausted"}:
        return batch
    now = datetime.now(UTC)
    batch.cancel_requested_at = now
    if batch.status == "capacity_blocked":
        batch.status = "cancelled"
        batch.finished_at = now
        batch.error = batch.error or "管理员取消容量阻塞批次"
        session.commit()
        session.refresh(batch)
        return batch
    tasks = list(session.scalars(select(ResearchTask).where(ResearchTask.batch_id == batch.id)))
    for task in tasks:
        if task.status == "queued":
            task.status = "cancelled"
            task.finished_at = now
        elif task.status == "running" and task.cancel_requested_at is None:
            task.cancel_requested_at = now
    session.flush()
    refresh_batch_status(session, batch_id=batch.id, now=now)
    if batch.status == "queued" and not any(task.status == "running" for task in tasks):
        batch.status = "cancelled"
        batch.finished_at = now
        session.commit()
        session.refresh(batch)
    return batch


def request_controlled_execution(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> ResearchTask | None:
    """记录用户对搜索与公开页面读取的显式授权。"""
    task = get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role)
    if task is None:
        return None
    if task.task_type != "manual":
        raise ResearchTaskStartError("仅手动研究任务可以请求受控读取")
    if task.status != "queued":
        raise ResearchTaskStartError("仅等待执行的研究任务可以开始")
    if task.execution_requested_at is None:
        task.execution_requested_at = datetime.now(UTC)
        task.current_step = "awaiting_research_worker"
        session.commit()
        session.refresh(task)
    return task


def claim_next_task(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 300,
    task_type: str | None = None,
    topic_prefix: str | None = None,
    require_source_urls: bool = False,
    require_execution_requested: bool = False,
    now: datetime | None = None,
) -> ResearchTask | None:
    """以行锁认领一个任务；租约过期的 running 任务可恢复。

    ``task_type`` 和 ``topic_prefix`` 仅用于隔离特定 Worker 可执行的任务集合；
    未传入时保持原有全队列认领行为。
    """
    current = now or datetime.now(UTC)
    conditions = [
        ResearchTask.cancel_requested_at.is_(None),
        or_(
            ResearchTask.status == "queued",
            (
                (ResearchTask.status == "running")
                & (ResearchTask.lease_until <= current)
            ),
        ),
    ]
    if task_type:
        conditions.append(ResearchTask.task_type == task_type)
    if topic_prefix:
        conditions.append(ResearchTask.topic.startswith(topic_prefix, autoescape=True))
    if require_source_urls:
        conditions.append(ResearchTask.source_urls != [])
    if require_execution_requested:
        conditions.append(ResearchTask.execution_requested_at.is_not(None))
    stmt = (
        select(ResearchTask)
        .where(*conditions)
        .order_by(ResearchTask.created_at, ResearchTask.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = session.scalars(stmt).first()
    if task is None:
        return None
    task.status = "running"
    task.worker_id = worker_id
    task.lease_until = current + timedelta(seconds=lease_seconds)
    task.attempts += 1
    task.started_at = task.started_at or current
    session.commit()
    session.refresh(task)
    return task


def renew_task_lease(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    lease_seconds: int = 300,
    now: datetime | None = None,
) -> ResearchTask | None:
    """延长当前 Worker 的有效租约；过期租约不可自助复活。"""
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    task.lease_until = current + timedelta(seconds=lease_seconds)
    session.commit()
    session.refresh(task)
    return task


def record_task_usage(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    search_queries_delta: int = 0,
    search_results_delta: int = 0,
    input_tokens_delta: int = 0,
    output_tokens_delta: int = 0,
    cost_delta: Decimal = Decimal("0"),
    current_step: str | None = None,
    now: datetime | None = None,
) -> ResearchTask | None:
    """记录当前 Worker 的增量用量；租约失效后拒绝旁路写入。"""
    deltas = (
        search_queries_delta,
        search_results_delta,
        input_tokens_delta,
        output_tokens_delta,
    )
    if any(delta < 0 for delta in deltas) or cost_delta < 0:
        raise ValueError("任务用量增量不能为负数")
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    _extend_active_task_lease(task, current)
    task.search_queries_used += search_queries_delta
    task.search_results_used += search_results_delta
    task.input_tokens_used += input_tokens_delta
    task.output_tokens_used += output_tokens_delta
    task.cost_amount += cost_delta
    if current_step is not None:
        task.current_step = current_step.strip() or None
    session.commit()
    session.refresh(task)
    return task


def reserve_task_usage(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    search_queries_delta: int = 0,
    search_results_delta: int = 0,
    input_tokens_delta: int = 0,
    output_tokens_delta: int = 0,
    cost_delta: Decimal = Decimal("0"),
    current_step: str | None = None,
    now: datetime | None = None,
) -> ResearchTask | None:
    """预留一组用量；超预算时原子拒绝，不写入部分计数。"""
    deltas = (
        search_queries_delta,
        search_results_delta,
        input_tokens_delta,
        output_tokens_delta,
    )
    if any(delta < 0 for delta in deltas) or cost_delta < 0:
        raise ValueError("任务用量增量不能为负数")
    current = now or datetime.now(UTC)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
            ResearchTask.lease_until > current,
        )
    )
    if task is None:
        return None
    _extend_active_task_lease(task, current)

    projected_queries = task.search_queries_used + search_queries_delta
    projected_results = task.search_results_used + search_results_delta
    projected_input_tokens = task.input_tokens_used + input_tokens_delta
    projected_output_tokens = task.output_tokens_used + output_tokens_delta
    projected_cost = task.cost_amount + cost_delta
    projected = (
        ("max_queries", Decimal(projected_queries)),
        ("max_results", Decimal(projected_results)),
        ("max_input_tokens", Decimal(projected_input_tokens)),
        ("max_output_tokens", Decimal(projected_output_tokens)),
        ("max_cost", projected_cost),
    )
    for key, value in projected:
        raw_limit = task.budget_snapshot.get(key)
        if raw_limit is None:
            continue
        try:
            limit = Decimal(str(raw_limit))
        except Exception as exc:  # noqa: BLE001 - 损坏预算必须安全拒绝
            raise ResearchBudgetExceeded(f"预算配置无效：{key}") from exc
        if limit < 0 or value > limit:
            raise ResearchBudgetExceeded(f"研究任务超过预算：{key}")

    task.search_queries_used = projected_queries
    task.search_results_used = projected_results
    task.input_tokens_used = projected_input_tokens
    task.output_tokens_used = projected_output_tokens
    task.cost_amount = projected_cost
    if current_step is not None:
        task.current_step = current_step.strip() or None
    session.commit()
    session.refresh(task)
    return task


def complete_task(
    session: Session,
    *,
    task_id: int,
    worker_id: str,
    succeeded: bool,
    error: str | None = None,
    terminal_status: str | None = None,
    now: datetime | None = None,
) -> ResearchTask | None:
    """仅当前 Worker 可以完成自己的租约；取消请求优先转为 cancelled。"""
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.worker_id == worker_id,
        )
    )
    if task is None:
        return None
    resolved_status = terminal_status or ("succeeded" if succeeded else "failed")
    if resolved_status not in {"succeeded", "failed", "skipped", "budget_exhausted"}:
        raise ValueError("不支持的研究任务终态")
    task.status = "cancelled" if task.cancel_requested_at else resolved_status
    task.error = error[:1000] if error else None
    task.finished_at = now or datetime.now(UTC)
    task.worker_id = None
    task.lease_until = None
    session.commit()
    session.refresh(task)
    if task.batch_id is not None:
        refresh_batch_status(session, batch_id=task.batch_id, now=task.finished_at)
    return task


def refresh_batch_status(
    session: Session, *, batch_id: int, now: datetime | None = None
) -> ResearchBatch | None:
    """按子任务终态刷新批次计数；不创建组合报告或发起外部调用。"""
    batch = session.get(ResearchBatch, batch_id)
    if batch is None:
        return None
    tasks = list(session.scalars(select(ResearchTask).where(ResearchTask.batch_id == batch.id)))
    counts = {
        "queued": sum(task.status == "queued" for task in tasks),
        "running": sum(task.status == "running" for task in tasks),
        "succeeded": sum(task.status == "succeeded" for task in tasks),
        "failed": sum(task.status == "failed" for task in tasks),
        "cancelled": sum(task.status == "cancelled" for task in tasks),
        "skipped": sum(task.status == "skipped" for task in tasks),
        "budget_exhausted": sum(task.status == "budget_exhausted" for task in tasks),
    }
    batch.supplier_count = len(tasks)
    batch.queued_count = counts["queued"]
    batch.running_count = counts["running"]
    batch.succeeded_count = counts["succeeded"]
    batch.failed_count = counts["failed"] + counts["cancelled"]
    batch.skipped_count = counts["skipped"]
    batch.budget_exhausted_count = counts["budget_exhausted"]
    if counts["running"]:
        batch.status = "running"
        batch.started_at = batch.started_at or (now or datetime.now(UTC))
    elif counts["queued"]:
        batch.status = "queued"
    elif batch.cancel_requested_at is not None:
        batch.status = "cancelled"
        batch.finished_at = now or datetime.now(UTC)
    elif counts["failed"] or counts["cancelled"] or counts["budget_exhausted"]:
        if counts["budget_exhausted"] and not counts["succeeded"] and not counts["failed"]:
            batch.status = "budget_exhausted"
        else:
            batch.status = "partial" if counts["succeeded"] else "failed"
        batch.finished_at = now or datetime.now(UTC)
    elif counts["skipped"]:
        batch.status = "partial"
        batch.finished_at = now or datetime.now(UTC)
    elif tasks:
        batch.status = "succeeded"
        batch.finished_at = now or datetime.now(UTC)
    session.commit()
    session.refresh(batch)
    if batch.status in {"succeeded", "partial", "failed", "budget_exhausted"}:
        create_batch_report(session, batch_id=batch.id)
        session.refresh(batch)
    return batch


def get_batch(
    session: Session, *, batch_id: int, owner_user_id: int, role: str
) -> ResearchBatch | None:
    """按任务相同的所有权规则读取周期批次。"""
    batch = session.get(ResearchBatch, batch_id)
    if batch is None:
        return None
    if role not in TASK_ADMIN_ROLES and batch.owner_user_id != owner_user_id:
        return None
    return batch


def list_batches(
    session: Session, *, owner_user_id: int, role: str
) -> list[ResearchBatch]:
    """按创建时间倒序列出当前用户可见的周期批次。"""
    stmt = select(ResearchBatch).order_by(
        ResearchBatch.created_at.desc(), ResearchBatch.id.desc()
    )
    if role not in TASK_ADMIN_ROLES:
        stmt = stmt.where(ResearchBatch.owner_user_id == owner_user_id)
    return list(session.scalars(stmt))


def _stable_batch_id(prefix: str, task_id: int, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{task_id}-{digest}"


def create_batch_report(
    session: Session, *, batch_id: int
) -> ResearchReport | None:
    """确定性聚合已完成子报告；未完成或已存在时不重复创建。"""
    batch = session.get(ResearchBatch, batch_id)
    if batch is None:
        return None
    existing = session.scalar(
        select(ResearchReport).where(ResearchReport.batch_id == batch.id)
    )
    if existing is not None:
        return existing
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.batch_id == batch.id)
            .order_by(ResearchTask.id)
        )
    )
    if not tasks or any(
        task.status
        not in {"succeeded", "failed", "cancelled", "skipped", "budget_exhausted"}
        for task in tasks
    ):
        return None

    supplier_names: dict[int, str] = {}
    for item in batch.supplier_snapshot:
        raw_supplier_id = item.get("supplier_id")
        if isinstance(raw_supplier_id, int):
            supplier_names[raw_supplier_id] = str(
                item.get("legal_name") or raw_supplier_id
            )
    facts: list[ResearchClaimDraft] = []
    inferences: list[ResearchClaimDraft] = []
    forecasts: list[ResearchClaimDraft] = []
    citations: list[ResearchCitationDraft] = []
    for task in tasks:
        child_report = session.scalar(
            select(ResearchReport)
            .where(ResearchReport.task_id == task.id)
            .order_by(ResearchReport.created_at.desc(), ResearchReport.id.desc())
        )
        if child_report is None:
            continue
        try:
            draft = ResearchReportDraft.model_validate(child_report.draft_payload)
        except ValueError:
            continue
        child_citations = {item.citation_id: item for item in draft.citations}
        used_citation_ids = {
            citation_id
            for claim in [*draft.facts, *draft.inferences, *draft.forecasts]
            for citation_id in claim.citation_ids
        }
        citation_id_map: dict[str, str] = {}
        for citation_id in sorted(used_citation_ids):
            citation = child_citations.get(citation_id)
            if citation is None or not citation.verified:
                continue
            mapped_id = _stable_batch_id("citation", task.id, citation_id)
            citation_id_map[citation_id] = mapped_id
            citations.append(citation.model_copy(update={"citation_id": mapped_id}))

        supplier_name = supplier_names.get(task.supplier_scope[0], str(task.supplier_scope[0]))
        for section, target in (
            (draft.facts, facts),
            (draft.inferences, inferences),
            (draft.forecasts, forecasts),
        ):
            for claim in section:
                mapped_citations = [
                    citation_id_map[citation_id]
                    for citation_id in claim.citation_ids
                    if citation_id in citation_id_map
                ]
                if not mapped_citations:
                    continue
                target.append(
                    claim.model_copy(
                        update={
                            "claim_id": _stable_batch_id("claim", task.id, claim.claim_id),
                            "text": f"[{supplier_name}] {claim.text}"[:4000],
                            "citation_ids": mapped_citations,
                        }
                    )
                )

    draft = ResearchReportDraft(
        title=f"{batch.period_key} 全供应商研究汇总",
        disclaimer=REQUIRED_DISCLAIMER,
        facts=facts,
        inferences=inferences,
        forecasts=forecasts,
        citations=citations,
    )
    return create_generated_report(
        session,
        task_id=None,
        batch_id=batch.id,
        draft=draft,
        model_version="deterministic-batch-v1",
    )


def create_report(
    session: Session,
    *,
    task_id: int,
    owner_user_id: int,
    role: str,
    draft: ResearchReportDraft,
    model_version: str | None,
) -> ResearchReport | None:
    """保存通过结构化校验的报告草稿；不改变任务状态或风险信号。"""
    task = get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role)
    if task is None:
        return None
    return create_generated_report(
        session,
        task_id=task.id,
        draft=draft,
        model_version=model_version,
    )


def create_generated_report(
    session: Session,
    *,
    task_id: int | None,
    batch_id: int | None = None,
    draft: ResearchReportDraft,
    model_version: str | None,
) -> ResearchReport:
    """保存已通过校验的系统生成或人工提交报告草稿。"""
    if (task_id is None) == (batch_id is None):
        raise ValueError("报告必须且只能绑定一个任务或批次")
    validate_report_draft(draft)
    payload = draft.model_dump(mode="json")
    report = ResearchReport(
        task_id=task_id,
        batch_id=batch_id,
        title=draft.title,
        draft_payload=payload,
        status="draft",
        review_status="pending",
        model_version=model_version,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def list_reports(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> list[ResearchReport] | None:
    """按任务读取报告草稿，并沿用任务创建者/管理员隔离规则。"""
    if get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role) is None:
        return None
    stmt = (
        select(ResearchReport)
        .where(ResearchReport.task_id == task_id)
        .order_by(ResearchReport.created_at.desc(), ResearchReport.id.desc())
    )
    return list(session.scalars(stmt))


def list_sources(
    session: Session, *, task_id: int, owner_user_id: int, role: str
) -> list[ResearchSource] | None:
    """按任务读取自动发现的公开来源，并沿用任务访问隔离规则。"""
    if get_task(session, task_id=task_id, owner_user_id=owner_user_id, role=role) is None:
        return None
    stmt = (
        select(ResearchSource)
        .where(ResearchSource.task_id == task_id)
        .order_by(ResearchSource.retrieved_at.desc(), ResearchSource.id.desc())
    )
    return list(session.scalars(stmt))
