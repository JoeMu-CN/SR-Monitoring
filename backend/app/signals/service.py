"""通用风险信号采集服务。

供 API 手动触发（POST /sources/{id}/run）和 Scheduler 定时任务共用：
    fetch -> normalize -> fingerprint -> 写入 raw_signals（指纹去重）-> 记录 collection_runs
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Final, TypeVar

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    merge_source_metadata,
    persist_signal_ingestions,
)
from app.signals.membership_persistence import apply_membership_snapshot
from app.signals.models import CollectionRun, DataSource
from app.signals.sources import PullSourceAdapter, SourceFetchError

_T = TypeVar("_T")

# 遗留 running 采集运行的保守收尾阈值：进程异常退出会留下无终态的 running
# 记录，超过该时长（默认 30 分钟）才判定为已中断，避免误伤仍在执行的采集。
COLLECTION_RUN_STALE_SECONDS: Final = 1800
STALE_COLLECTION_RUN_ERROR: Final = "采集超时未完成（进程异常退出），已自动标记失败"


def asyncio_run[T](coro: Coroutine[object, object, T]) -> T:
    """在同步上下文中运行异步协程（采集适配器接口为 async）。"""
    return asyncio.run(coro)


class SourceNotCollectable(ValueError):
    """信息源不支持 HTTP 拉取（如 manual-json 走文件上传）。"""


class CollectionFailed(RuntimeError):
    """采集执行失败，已记录失败的 collection_run。"""


class CollectionDeferred(RuntimeError):
    """请求受控延后（域名冷却/租约/节流），未产生失败 collection_run。"""

    error_kind = "deferred"


def collect_source(
    session: Session, source: DataSource, adapter: PullSourceAdapter
) -> CollectionRun:
    return asyncio_run(collect_source_async(session, source, adapter))


async def collect_source_async(
    session: Session, source: DataSource, adapter: PullSourceAdapter
) -> CollectionRun:
    """执行一次拉取式采集，写入新信号并返回本次运行记录。

    指纹相同的信号通过唯一约束去重（on_conflict_do_nothing）。
    """
    run = CollectionRun(source_id=source.id, status="running")
    session.add(run)
    session.commit()
    try:
        items = None
        try:
            items = await adapter.fetch()
        except SourceFetchError as exc:
            if exc.error_kind == CollectionDeferred.error_kind:
                # 受控延后（域名冷却/租约/节流）不是采集失败：删除临时运行，
                # 不落 failed 记录、不污染健康观测。
                _discard_provisional_run(session, run.id)
                raise CollectionDeferred(str(exc)) from exc
            _fail_run(session, run.id, str(exc))
            raise CollectionFailed(str(exc)) from exc
        ingestions: list[SignalIngestion] = []
        collected_at = datetime.now(UTC)
        for item in items:
            try:
                signal = merge_source_metadata(adapter.normalize(item), item)
            except (SourceFetchError, ValidationError):
                continue
            ingestions.append(
                SignalIngestion(
                    source=source,
                    signal=signal,
                    fingerprint=adapter.fingerprint(signal),
                    collected_at=collected_at,
                    authority=LifecycleAuthority("adapter", adapter.source_code),
                )
            )
        created = persist_signal_ingestions(session, ingestions)
        stored_run = session.get(CollectionRun, run.id)
        assert stored_run is not None
        snapshot_keys = frozenset(
            item.signal.external_id
            for item in ingestions
            if item.signal.external_id
        )
        apply_membership_snapshot(
            session,
            source,
            adapter,
            stored_run,
            snapshot_keys,
            len(snapshot_keys),
            collected_at,
        )
        stored_run.status = "succeeded"
        stored_run.finished_at = datetime.now(UTC)
        stored_run.fetched_count = len(ingestions)
        stored_run.created_count = created
        stored_run.duplicate_count = len(ingestions) - created
        session.commit()
    except CollectionDeferred:
        # 已在 fetch 阶段清理临时运行；不得再被下方宽泛捕获包成采集失败。
        raise
    except Exception as exc:
        if isinstance(exc, CollectionFailed):
            raise
        _fail_run(session, run.id, f"采集异常: {exc}")
        raise CollectionFailed(str(exc)) from exc
    stored_run = session.get(CollectionRun, run.id)
    assert stored_run is not None
    return stored_run


def _discard_provisional_run(session: Session, run_id: int) -> None:
    """删除 fetch 阶段判定延后的临时运行：回滚 -> 按 id 取回 -> 删除 -> 提交。"""
    session.rollback()
    run = session.get(CollectionRun, run_id)
    if run is not None:
        session.delete(run)
        session.commit()


def _fail_run(session: Session, run_id: int, message: str) -> None:
    session.rollback()
    run = session.get(CollectionRun, run_id)
    if run is not None:
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.error = message[:2000]
        session.commit()


def finalize_stale_collection_runs(
    session: Session,
    *,
    stale_before: datetime,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """保守收尾超过阈值仍停留在 ``running`` 的遗留采集运行。

    进程异常退出会留下永久 ``running`` 的 collection_runs，阻塞来源健康观测
    与后续判定。外部是否仍在执行无法从数据库可靠推断，因此仅在明显超时
    （``COLLECTION_RUN_STALE_SECONDS``，默认 30 分钟）后按失败收尾并写入稳定
    错误文本；新鲜 running 与终态记录保持不变。只操作传入的 Session。
    """
    current = now or datetime.now(UTC)
    if stale_before.tzinfo is None or current.tzinfo is None:
        raise ValueError("采集运行收尾时间必须带时区")
    if stale_before > current:
        raise ValueError("采集运行收尾阈值不能晚于当前时间")
    if limit < 1:
        raise ValueError("采集运行收尾批量必须大于 0")
    stale_runs = list(
        session.scalars(
            select(CollectionRun)
            .where(
                CollectionRun.status == "running",
                CollectionRun.started_at < stale_before,
            )
            .order_by(CollectionRun.started_at, CollectionRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for run in stale_runs:
        run.status = "failed"
        run.finished_at = current
        run.error = STALE_COLLECTION_RUN_ERROR
    if stale_runs:
        session.commit()
    return len(stale_runs)
