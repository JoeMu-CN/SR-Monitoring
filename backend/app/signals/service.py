"""通用风险信号采集服务。

供 API 手动触发（POST /sources/{id}/run）和 Scheduler 定时任务共用：
    fetch -> normalize -> fingerprint -> 写入 raw_signals（指纹去重）-> 记录 collection_runs
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.signals.models import CollectionRun, DataSource, RawSignal
from app.signals.sources import PullSourceAdapter, SourceFetchError

_T = TypeVar("_T")


def asyncio_run[T](coro: Coroutine[object, object, T]) -> T:
    """在同步上下文中运行异步协程（采集适配器接口为 async）。"""
    return asyncio.run(coro)


class SourceNotCollectable(ValueError):
    """数据源不支持 HTTP 拉取（如 manual-json 走文件上传）。"""


class CollectionFailed(RuntimeError):
    """采集执行失败，已记录失败的 collection_run。"""


def collect_source(
    session: Session, source: DataSource, adapter: PullSourceAdapter
) -> CollectionRun:
    """执行一次拉取式采集，写入新信号并返回本次运行记录。

    指纹相同的信号通过唯一约束去重（on_conflict_do_nothing）。
    """
    run = CollectionRun(source_id=source.id, status="running")
    session.add(run)
    session.commit()
    session.refresh(run)
    try:
        items = None
        try:
            items = asyncio_run(adapter.fetch())
        except SourceFetchError as exc:
            _fail_run(session, run.id, str(exc))
            raise CollectionFailed(str(exc)) from exc
        rows: list[dict[str, object]] = []
        for item in items:
            try:
                signal = adapter.normalize(item)
            except SourceFetchError:
                continue
            rows.append(
                {
                    "source_id": source.id,
                    "external_id": signal.external_id,
                    "title": signal.title,
                    "content": signal.content,
                    "url": str(signal.url) if signal.url else None,
                    "published_at": signal.published_at,
                    "fingerprint": adapter.fingerprint(signal),
                    "raw_data": signal.model_dump(mode="json"),
                }
            )
        created = 0
        if rows:
            result = session.execute(
                insert(RawSignal)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=[RawSignal.source_id, RawSignal.fingerprint]
                )
                .returning(RawSignal.id)
            )
            created = len(result.scalars().all())
        stored_run = session.get(CollectionRun, run.id)
        assert stored_run is not None
        stored_run.status = "succeeded"
        stored_run.finished_at = datetime.now(UTC)
        stored_run.fetched_count = len(rows)
        stored_run.created_count = created
        stored_run.duplicate_count = len(rows) - created
        session.commit()
    except Exception as exc:
        if isinstance(exc, CollectionFailed):
            raise
        _fail_run(session, run.id, f"采集异常: {exc}")
        raise CollectionFailed(str(exc)) from exc
    stored_run = session.get(CollectionRun, run.id)
    assert stored_run is not None
    return stored_run


def _fail_run(session: Session, run_id: int, message: str) -> None:
    session.rollback()
    run = session.get(CollectionRun, run_id)
    if run is not None:
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.error = message[:2000]
        session.commit()
