"""until_superseded 信号"同 dedup_key 重采"不得顶掉唯一 active 版本。

现场证据（2026-10-20 大宗商品期货行情）：
`commodity-futures` 每 6 小时采集一次，external_id 按日固定
（``fut-{code}-{YYYYMMDD}``），因此同一交易日内多次采集会得到**相同的 dedup_key**
但**不同的 fingerprint**（价格在变）。旧实现先把 active 版本标成 superseded，
再插入新行；插入被 ``(source_id, dedup_key)`` 唯一约束挡下（on_conflict_do_nothing
返回空），于是"旧版本被下线、新版本不存在"，信源有效记录数掉到 0。
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.signals.identity import compute_dedup_key
from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    persist_signal_ingestions,
)
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.validity import ValidityProfile, ValidityState

VALIDITY_KEY = "fut-nf_CU0"
EXTERNAL_ID = "fut-nf_CU0-2026-10-20"
TITLE = "沪铜主力 78500 元/吨（+1.20%）"


@dataclass(frozen=True, slots=True)
class _Recollect:
    fingerprint: str
    collected_at: datetime


@pytest.fixture
def futures_source_id() -> Generator[int]:
    code = f"futures-recollect-{uuid4().hex}"
    with SessionLocal.begin() as session:
        source = DataSource(
            code=code,
            name="期货重采测试信源",
            source_type="macro",
            credibility=80,
            enabled=True,
            adapter_status="builtin",
            adapter_version=0,
            auth_type="none",
            login_config={},
            adapter_config={},
            validity_policy={"mode": "until_superseded", "fixed_days": 3},
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


def _ingestion(source: DataSource, recollect: _Recollect) -> SignalIngestion:
    return SignalIngestion(
        source=source,
        signal=ManualSignalInput(
            external_id=EXTERNAL_ID,
            title=TITLE,
            content=f"最新价随行情变动，fingerprint={recollect.fingerprint}",
            validity_profile=ValidityProfile.MARKET_PRICE_POINT,
            validity_key=VALIDITY_KEY,
        ),
        fingerprint=recollect.fingerprint,
        dedup_key=compute_dedup_key(
            source.code,
            external_id=EXTERNAL_ID,
            url="https://finance.sina.com.cn/futures/quotes/CU0.shtml",
            title=TITLE,
        ),
        collected_at=recollect.collected_at,
        authority=LifecycleAuthority("adapter", "recollect-test"),
    )


def _persist(source_id: int, recollect: _Recollect) -> int:
    with SessionLocal.begin() as session:
        source = session.get(DataSource, source_id)
        assert source is not None
        return persist_signal_ingestions(session, [_ingestion(source, recollect)])


def _rows(source_id: int) -> list[RawSignal]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(RawSignal)
                .where(RawSignal.source_id == source_id)
                .order_by(RawSignal.id)
            )
        )


def test_same_dedup_key_recollect_keeps_active_version(futures_source_id: int) -> None:
    # Given：当日第一次采集已落库并处于 active。
    assert _persist(
        futures_source_id,
        _Recollect("fp-first", datetime(2026, 10, 20, 0, 19, tzinfo=UTC)),
    ) == 1

    # When：同一天内价格变动后的重采（同 dedup_key、不同 fingerprint、更晚时间）。
    created = _persist(
        futures_source_id,
        _Recollect("fp-second", datetime(2026, 10, 20, 6, 0, tzinfo=UTC)),
    )

    # Then：不算新增，且唯一已落库版本仍为 active（有效记录数不会掉到 0）。
    assert created == 0
    rows = _rows(futures_source_id)
    assert len(rows) == 1
    assert rows[0].validity_state == ValidityState.ACTIVE
    assert rows[0].fingerprint == "fp-first"


def test_recollect_is_idempotent_over_multiple_runs(futures_source_id: int) -> None:
    """连采三轮（每日 4 次调度的现实场景）后仍然只有一条 active。"""
    base = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    for hour in (0, 6, 12, 18):
        _persist(
            futures_source_id,
            _Recollect(f"fp-{hour:02d}", base.replace(hour=hour)),
        )

    rows = _rows(futures_source_id)
    assert len(rows) == 1
    assert [row.validity_state for row in rows] == [ValidityState.ACTIVE]
