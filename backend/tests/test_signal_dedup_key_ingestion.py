"""采集入库按稳定 dedup_key 去重的集成测试。

覆盖真实写入路径：collect_source -> persist_signal_ingestions ->
``ON CONFLICT (source_id, dedup_key) DO NOTHING``，验证易变字段（URL scheme、
价格、官方修订标题）变化不再产生重复行。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.signals.ingestion import (
    LifecycleAuthority,
    SignalIngestion,
    persist_signal_ingestions,
)
from app.signals.models import DataSource, RawSignal
from app.signals.schemas import ManualSignalInput
from app.signals.service import collect_source
from app.signals.sources import RawSourceItem

MOFCOM_CODE = "mofcom-entity-detail"


def _whole_model_fingerprint(signal: ManualSignalInput) -> str:
    """复现内置适配器的整模型指纹口径。"""
    canonical = json.dumps(
        signal.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _FakeMofcomAdapter:
    """模拟商务部详情适配器：同一批实体，URL 写法/价格/措辞可漂移。"""

    source_code = MOFCOM_CODE
    authoritative_full_snapshot = False

    def __init__(self, *, scheme: str, price: float, suffix: str = "") -> None:
        self._scheme = scheme
        self._price = price
        self._suffix = suffix

    async def fetch(self, cursor: str | None = None) -> list[RawSourceItem]:
        del cursor
        return [
            RawSourceItem(
                external_id="mofcom-entity-laffitte",
                title=f"拉法特集团{self._suffix}",
                content=f"官方公告正文：涉及金额 {self._price} 万欧元。",
                url=f"{self._scheme}://aqygzj.mofcom.gov.cn/article/2026/30",
            )
        ]

    def normalize(self, item: RawSourceItem) -> ManualSignalInput:
        return ManualSignalInput(
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            url=item.url,
        )

    def fingerprint(self, signal: ManualSignalInput) -> str:
        return _whole_model_fingerprint(signal)

    async def healthcheck(self) -> object:
        return object()


def _mofcom_source(db_session: Session) -> DataSource:
    source = db_session.scalar(select(DataSource).where(DataSource.code == MOFCOM_CODE))
    assert source is not None, "迁移 0050 应已注册 mofcom-entity-detail 信息源"
    return source


def test_scheme_flip_and_price_churn_do_not_insert_duplicate(
    db_session: Session,
) -> None:
    # Given: 首轮以 HTTP 明文 + 初始价格采集到一条实体信号。
    source = _mofcom_source(db_session)
    first = collect_source(
        db_session, source, _FakeMofcomAdapter(scheme="http", price=120.0)
    )
    assert first.created_count == 1
    before = db_session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .where(RawSignal.source_id == source.id)
    )
    assert before == 1
    stored = db_session.scalar(
        select(RawSignal).where(RawSignal.source_id == source.id).order_by(RawSignal.id)
    )
    assert stored is not None

    # When: 官方切到 HTTPS、价格与标题措辞同时变化后再采一轮。
    second = collect_source(
        db_session,
        source,
        _FakeMofcomAdapter(scheme="https", price=135.5, suffix="（更新）"),
    )

    # Then: 0 新增、计数不变；旧行内容不被覆盖（去重而非更新）。
    assert second.created_count == 0
    assert second.duplicate_count == 1
    after = db_session.scalar(
        select(func.count())
        .select_from(RawSignal)
        .where(RawSignal.source_id == source.id)
    )
    assert after == before
    db_session.refresh(stored)
    assert stored.url == "http://aqygzj.mofcom.gov.cn/article/2026/30"
    assert stored.title == "拉法特集团"


def test_old_fingerprint_really_would_have_duplicated() -> None:
    """反向证明：整模型指纹口径下这两轮确实不同（否则上面的断言毫无意义）。"""
    http_signal = ManualSignalInput(
        external_id="mofcom-entity-laffitte",
        title="拉法特集团",
        content="官方公告正文：涉及金额 120.0 万欧元。",
        url="http://aqygzj.mofcom.gov.cn/article/2026/30",
    )
    https_signal = ManualSignalInput(
        external_id="mofcom-entity-laffitte",
        title="拉法特集团（更新）",
        content="官方公告正文：涉及金额 135.5 万欧元。",
        url="https://aqygzj.mofcom.gov.cn/article/2026/30",
    )

    assert _whole_model_fingerprint(http_signal) != _whole_model_fingerprint(https_signal)


def test_direct_ingestion_dedups_on_dedup_key_not_fingerprint(db_session: Session) -> None:
    """入库层本身按 dedup_key 去重：fingerprint 变化不影响新增计数。"""
    source = _mofcom_source(db_session)
    collected_at = datetime(2026, 9, 6, 12, tzinfo=UTC)
    first = ManualSignalInput(
        external_id="mee-like-1",
        title="环境部通报",
        content="正文甲",
        url="https://mee.gov.cn/notice/1",
    )
    second = ManualSignalInput(
        external_id="mee-like-1",
        title="环境部通报（修订）",
        content="正文乙",
        url="https://mee.gov.cn/notice/1",
    )

    def _ingest(signal: ManualSignalInput, fingerprint: str) -> int:
        return persist_signal_ingestions(
            db_session,
            [
                SignalIngestion(
                    source=source,
                    signal=signal,
                    fingerprint=fingerprint,
                    dedup_key="stable-identity-for-mee-like-1",
                    collected_at=collected_at,
                    authority=LifecycleAuthority("adapter", MOFCOM_CODE),
                )
            ],
        )

    assert _ingest(first, "fingerprint-a") == 1
    assert _ingest(second, "fingerprint-b") == 0
    rows = list(
        db_session.scalars(select(RawSignal).where(RawSignal.source_id == source.id))
    )
    assert len(rows) == 1
    assert rows[0].fingerprint == "fingerprint-a"