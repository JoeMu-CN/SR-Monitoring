"""until_superseded 同 key 的真实 PostgreSQL 并发集成测试。"""

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier
from typing import Final
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    persist_signal_ingestions,
)
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.validity import ValidityProfile, ValidityState

VALIDITY_KEY: Final = "concurrent-supersession-key"


@dataclass(frozen=True, slots=True)
class _Candidate:
    external_id: str
    published_at: datetime
    collected_at: datetime
    fingerprint: str


@pytest.fixture
def supersession_source_id() -> Generator[int]:
    code = f"supersession-concurrency-{uuid4().hex}"
    with SessionLocal.begin() as session:
        source = DataSource(
            code=code,
            name="并发替代测试信源",
            source_type="api",
            credibility=80,
            enabled=True,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
        )
        session.add(source)
        session.flush()
        source_id = source.id
    try:
        yield source_id
    finally:
        with SessionLocal.begin() as session:
            session.execute(delete(RawSignal).where(RawSignal.source_id == source_id))
            session.execute(delete(DataSource).where(DataSource.id == source_id))


def _ingestion(source: DataSource, candidate: _Candidate) -> SignalIngestion:
    return SignalIngestion(
        source=source,
        signal=ManualSignalInput(
            external_id=candidate.external_id,
            title=f"PMI {candidate.external_id}",
            content=f"制造业采购经理指数（PMI）：49.2%（{candidate.external_id}）",
            published_at=candidate.published_at,
            validity_profile=ValidityProfile.MONTHLY_MACRO_INDICATOR,
            validity_key=VALIDITY_KEY,
        ),
        fingerprint=candidate.fingerprint,
        collected_at=candidate.collected_at,
        authority=LifecycleAuthority("adapter", "concurrency-test"),
    )


def _submit_concurrently(
    source_id: int, candidates: tuple[_Candidate, _Candidate]
) -> list[int]:
    barrier = Barrier(2)

    def submit(candidate: _Candidate) -> int:
        with SessionLocal.begin() as session:
            source = session.get(DataSource, source_id)
            assert source is not None
            ingestion = _ingestion(source, candidate)
            barrier.wait(timeout=10)
            return persist_signal_ingestions(session, [ingestion])

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(submit, candidates))


def _load_rows(source_id: int) -> list[RawSignal]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(RawSignal)
                .where(
                    RawSignal.source_id == source_id,
                    RawSignal.validity_key == VALIDITY_KEY,
                )
                .order_by(RawSignal.fingerprint)
            )
        )


def _assert_duplicate_is_idempotent(source_id: int, candidate: _Candidate) -> None:
    with SessionLocal.begin() as session:
        source = session.get(DataSource, source_id)
        assert source is not None
        assert persist_signal_ingestions(session, [_ingestion(source, candidate)]) == 0


def test_concurrent_supersession_out_of_order_authority_keeps_one_active(
    supersession_source_id: int,
) -> None:
    # Given：同 key 的新旧权威版本已在两个独立事务中就绪。
    older = _Candidate(
        external_id="stats-pmi-2026-08",
        published_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        collected_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        fingerprint="fp-older",
    )
    newer = _Candidate(
        external_id="stats-pmi-2026-09",
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        collected_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
        fingerprint="fp-newer",
    )

    # When：Barrier 同步放行两个 Session；任一线程异常（含 IntegrityError）会直接失败。
    created = _submit_concurrently(supersession_source_id, (older, newer))
    _assert_duplicate_is_idempotent(supersession_source_id, newer)

    # Then：终态只保留较新权威版本 active，重复指纹不新增记录。
    assert created == [1, 1]
    rows = _load_rows(supersession_source_id)
    assert len(rows) == 2
    active = [row for row in rows if row.validity_state == ValidityState.ACTIVE]
    assert [row.fingerprint for row in active] == [newer.fingerprint]
    older_row = next(row for row in rows if row.fingerprint == older.fingerprint)
    assert older_row.validity_state == ValidityState.SUPERSEDED
    assert older_row.valid_until == older_row.valid_from


def test_concurrent_supersession_same_authority_uses_fingerprint_winner(
    supersession_source_id: int,
) -> None:
    # Given：同 key、同权威时间但 fingerprint 不同的两个版本。
    authority_time = datetime(2026, 9, 1, 9, tzinfo=UTC)
    collected_at = datetime(2026, 9, 1, 10, tzinfo=UTC)
    larger = _Candidate("stats-pmi-b", authority_time, collected_at, "fp-b")
    smaller = _Candidate("stats-pmi-a", authority_time, collected_at, "fp-a")

    # When：Barrier 同步放行两个独立 Session，并再次提交赢家指纹验证幂等。
    created = _submit_concurrently(supersession_source_id, (larger, smaller))
    _assert_duplicate_is_idempotent(supersession_source_id, smaller)

    # Then：终态与线程取得锁的顺序无关，字典序较小 fingerprint 唯一 active。
    assert created == [1, 1]
    rows = _load_rows(supersession_source_id)
    assert len(rows) == 2
    active = [row for row in rows if row.validity_state == ValidityState.ACTIVE]
    assert [row.fingerprint for row in active] == [smaller.fingerprint]
    conflicted = next(row for row in rows if row.fingerprint == larger.fingerprint)
    assert conflicted.validity_state == ValidityState.CONFLICTED
    assert conflicted.valid_until == conflicted.valid_from
    assert conflicted.validity_reason["code"] == "same_authority_time_conflict"
