"""独立信号消费循环（scheduler.consume）行为测试。

覆盖：开关关闭不处理、时间预算耗尽即停、队列全零只跑一批、拿不到 advisory
lock 直接跳过。advisory lock 与 SessionLocal 全部 monkeypatch 隔离，不依赖
PostgreSQL 会话锁的真实行为。
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import app.scheduler.consume as consume_module
from app.scheduler.jobs import BatchResult


class _FakeConnection:
    """advisory lock 用的最小连接替身：不接触数据库。"""


def _install_engine(monkeypatch: pytest.MonkeyPatch) -> _FakeConnection:
    fake = _FakeConnection()
    monkeypatch.setattr(
        consume_module, "engine", SimpleNamespace(connect=lambda: nullcontext(fake))
    )
    return fake


def _capture_observations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []

    def _started(job_key: str, **kwargs: object) -> bool:
        events.append(("started", {"job_key": job_key, **kwargs}))
        return True

    def _result(job_key: str, **kwargs: object) -> bool:
        events.append(("result", {"job_key": job_key, **kwargs}))
        return True

    monkeypatch.setattr(consume_module.runtime, "record_job_started", _started)
    monkeypatch.setattr(consume_module.runtime, "record_job_result", _result)
    return events


def test_consume_job_returns_zero_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGNAL_CONSUME_ENABLED=false：连会话都不开，更不处理任何批次。"""
    calls: list[int] = []
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_ENABLED", False)
    monkeypatch.setattr(
        consume_module,
        "engine",
        SimpleNamespace(
            connect=lambda: (_ for _ in ()).throw(
                AssertionError("关闭时不应打开数据库连接")
            )
        ),
    )
    monkeypatch.setattr(
        consume_module,
        "process_signal_batch",
        lambda batch, **kwargs: calls.append(batch) or BatchResult(1, 0, 0),
    )

    assert consume_module.consume_pending_signals_job() == 0
    assert calls == []


def test_consume_job_stops_when_time_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """时间预算耗尽即停：两批后越过 deadline，不再继续拉下一批。"""
    _install_engine(monkeypatch)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_ENABLED", True)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_BATCH_SIZE", 5)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_TIME_BUDGET_SECONDS", 15)
    monkeypatch.setattr(consume_module, "_try_acquire_consume_lock", lambda session: True)
    monkeypatch.setattr(consume_module, "_release_consume_lock", lambda session: None)
    events = _capture_observations(monkeypatch)

    # 首次取基准（0.0）→ 第一批后 1.0（仍在预算内）→ 第二批后 100.0（超预算）。
    ticks = iter([0.0, 1.0, 100.0])
    monkeypatch.setattr(
        consume_module, "time", SimpleNamespace(monotonic=lambda: next(ticks))
    )
    batches: list[int] = []

    def _batch(batch: int, **kwargs: object) -> BatchResult:
        batches.append(batch)
        return BatchResult(processed=1, filtered=0, failed=0)

    monkeypatch.setattr(consume_module, "process_signal_batch", _batch)

    total = consume_module.consume_pending_signals_job()

    assert batches == [5, 5]
    assert total == 2
    # 每批开始写 running，循环结束后写一次累计结论。
    assert [kind for kind, _ in events] == ["started", "started", "result"]
    assert events[-1][1]["succeeded"] is True
    assert events[-1][1]["processed"] == 2


def test_consume_job_stops_after_single_all_zero_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """队列全零（无处理/过滤/失败）时只调用一次批次即收工。"""
    _install_engine(monkeypatch)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_ENABLED", True)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_TIME_BUDGET_SECONDS", 60)
    monkeypatch.setattr(consume_module, "_try_acquire_consume_lock", lambda session: True)
    monkeypatch.setattr(consume_module, "_release_consume_lock", lambda session: None)
    _capture_observations(monkeypatch)
    ticks = iter([0.0, 0.1, 0.2, 0.3])
    monkeypatch.setattr(
        consume_module, "time", SimpleNamespace(monotonic=lambda: next(ticks))
    )
    calls: list[int] = []

    def _batch(batch: int, **kwargs: object) -> BatchResult:
        calls.append(batch)
        return BatchResult(processed=0, filtered=0, failed=0)

    monkeypatch.setattr(consume_module, "process_signal_batch", _batch)

    assert consume_module.consume_pending_signals_job() == 0
    assert calls == [consume_module.SIGNAL_CONSUME_BATCH_SIZE]


def test_consume_job_skips_when_advisory_lock_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拿不到 advisory lock：返回 0、不处理、也不去解锁别人的锁。"""
    _install_engine(monkeypatch)
    monkeypatch.setattr(consume_module, "SIGNAL_CONSUME_ENABLED", True)
    monkeypatch.setattr(
        consume_module, "_try_acquire_consume_lock", lambda session: False
    )
    released: list[int] = []
    monkeypatch.setattr(
        consume_module,
        "_release_consume_lock",
        lambda session: released.append(1),
    )
    calls: list[int] = []
    monkeypatch.setattr(
        consume_module,
        "process_signal_batch",
        lambda batch, **kwargs: calls.append(batch) or BatchResult(1, 0, 0),
    )

    assert consume_module.consume_pending_signals_job() == 0
    assert calls == []
    assert released == []
