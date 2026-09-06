"""until_superseded 信号的原子替代语义。

在调用方事务内对 (source_id, validity_key) 取 PostgreSQL 双 int4 advisory
lock，锁内按权威时间与 SHA-256 fingerprint 决定替代/冲突关系，并保证部分
唯一索引（每个 key 最多一个 active）作为最终不变量。
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.signals.models import RawSignal
from app.signals.validity import ValidityMode, ValidityState

if TYPE_CHECKING:
    from app.signals.ingestion import SignalIngestion, SignalValiditySnapshot


def is_supersession_candidate(
    ingestion: SignalIngestion, snapshot: SignalValiditySnapshot
) -> bool:
    """是否为需要按权威时间原子替代的 until_superseded 活跃信号。"""
    return (
        snapshot.mode is ValidityMode.UNTIL_SUPERSEDED
        and snapshot.state is ValidityState.ACTIVE
        and ingestion.signal.validity_key is not None
    )


def advisory_key1(source_id: int) -> int:
    """advisory lock 第一键：校验为正 int32 的 source_id。"""
    if not 0 < source_id <= 2**31 - 1:
        from app.signals.ingestion import SignalIngestionError

        raise SignalIngestionError("source_id_out_of_int32_range")
    return source_id


def advisory_key2(validity_key: str) -> int:
    """advisory lock 第二键：sha256(NFC(key)) 前 4 字节 big-endian signed int32。"""
    normalized = unicodedata.normalize("NFC", validity_key).encode("utf-8")
    digest = hashlib.sha256(normalized).digest()[:4]
    return int.from_bytes(digest, "big", signed=True)


def _advisory_lock(session: Session, source_id: int, validity_key: str) -> None:
    """对 (source_id, validity_key) 取事务级双 int4 advisory lock。"""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": advisory_key1(source_id), "k2": advisory_key2(validity_key)},
    )


def insert_supersession(
    session: Session,
    ingestion: SignalIngestion,
    snapshot: SignalValiditySnapshot,
    row: dict[str, object],
) -> int:
    """在 advisory lock 内解析替代/冲突并插入，唯一冲突有限重试一次。"""
    validity_key = ingestion.signal.validity_key
    assert validity_key is not None  # is_supersession_candidate 已保证
    _advisory_lock(session, ingestion.source.id, validity_key)
    for _attempt in range(2):
        _resolve_supersession(session, ingestion, snapshot, row)
        try:
            with session.begin_nested():
                result = session.execute(
                    insert(RawSignal)
                    .values(row)
                    .on_conflict_do_nothing(
                        index_elements=[RawSignal.source_id, RawSignal.fingerprint]
                    )
                    .returning(RawSignal.id)
                )
                inserted = result.scalars().first()
            return 1 if inserted is not None else 0
        except IntegrityError:
            # 部分唯一索引冲突：重查并重试一次，不泄漏 IntegrityError。
            continue
    from app.signals.ingestion import SignalIngestionError

    raise SignalIngestionError("supersession_unique_conflict")


def _resolve_supersession(
    session: Session,
    ingestion: SignalIngestion,
    snapshot: SignalValiditySnapshot,
    row: dict[str, object],
) -> None:
    """在锁内决定新信号与既有 active 版本的替代/冲突关系。"""
    existing = session.scalar(
        select(RawSignal)
        .where(
            RawSignal.source_id == ingestion.source.id,
            RawSignal.validity_key == ingestion.signal.validity_key,
            RawSignal.validity_state == ValidityState.ACTIVE,
            RawSignal.validity_mode == ValidityMode.UNTIL_SUPERSEDED,
        )
        .with_for_update()
    )
    if existing is None:
        return
    new_from = snapshot.valid_from
    old_from = existing.valid_from
    assert old_from is not None  # active 信号按 DB 约束必有 valid_from
    new_fp = ingestion.fingerprint
    old_fp = existing.fingerprint
    if new_fp == old_fp:
        # 重复项按指纹幂等：不替代、不插入，旧版本保持 active。
        return
    if new_from > old_from:
        _mark_existing_superseded(existing, old_from, ingestion)
        _refresh_existing_support(session, existing, ingestion.collected_at)
        return
    if new_from < old_from:
        # 乱序旧数据不反向替代：新信号留库但标为 superseded。
        _mark_row_superseded(row, new_from, ingestion, snapshot)
        return
    # 权威时间相同且指纹不同：按 SHA-256 fingerprint 字典序决定 winner。
    if new_fp < old_fp:
        _mark_existing_conflicted(existing, old_from, ingestion)
        _refresh_existing_support(session, existing, ingestion.collected_at)
        return
    _mark_row_conflicted(row, new_from, ingestion, snapshot)


def _mark_existing_superseded(
    existing: RawSignal, valid_until: datetime, ingestion: SignalIngestion
) -> None:
    existing.validity_state = ValidityState.SUPERSEDED
    existing.valid_until = valid_until
    existing.validity_reason = {
        "code": "superseded_by_newer_version",
        "anchor_source": _existing_anchor(existing),
        "details": {
            "superseding_external_id": ingestion.signal.external_id,
            "superseding_fingerprint": ingestion.fingerprint,
        },
    }


def _refresh_existing_support(
    session: Session, existing: RawSignal, now_utc: datetime
) -> None:
    from app.risks.validity import refresh_signal_events

    refresh_signal_events(session, existing, now_utc=now_utc)


def _mark_row_superseded(
    row: dict[str, object],
    valid_until: datetime,
    ingestion: SignalIngestion,
    snapshot: SignalValiditySnapshot,
) -> None:
    row["validity_state"] = ValidityState.SUPERSEDED
    row["valid_until"] = valid_until
    row["validity_reason"] = {
        "code": "superseded_by_newer_version",
        "anchor_source": snapshot.reason["anchor_source"],
        "details": {
            "superseding_external_id": ingestion.signal.external_id,
            "superseding_fingerprint": ingestion.fingerprint,
        },
    }


def _mark_existing_conflicted(
    existing: RawSignal, valid_until: datetime, ingestion: SignalIngestion
) -> None:
    existing.validity_state = ValidityState.CONFLICTED
    existing.valid_until = valid_until
    existing.validity_reason = {
        "code": "same_authority_time_conflict",
        "anchor_source": _existing_anchor(existing),
        "details": {
            "conflicting_external_id": ingestion.signal.external_id,
            "conflicting_fingerprint": ingestion.fingerprint,
        },
    }


def _mark_row_conflicted(
    row: dict[str, object],
    valid_until: datetime,
    ingestion: SignalIngestion,
    snapshot: SignalValiditySnapshot,
) -> None:
    row["validity_state"] = ValidityState.CONFLICTED
    row["valid_until"] = valid_until
    row["validity_reason"] = {
        "code": "same_authority_time_conflict",
        "anchor_source": snapshot.reason["anchor_source"],
        "details": {
            "conflicting_external_id": ingestion.signal.external_id,
            "conflicting_fingerprint": ingestion.fingerprint,
        },
    }


def _existing_anchor(existing: RawSignal) -> str:
    reason = existing.validity_reason or {}
    anchor = reason.get("anchor_source")
    if anchor in {
        "published_at",
        "collected_at",
        "official_valid_until",
        "event_end",
        "legacy",
    }:
        return anchor
    return "published_at"
