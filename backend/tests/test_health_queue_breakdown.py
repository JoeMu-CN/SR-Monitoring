"""健康聚合：待分析队列按信源类型拆解，且既有响应字段保持不变。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.scheduler.health import build_monitoring_health
from app.scheduler.health_schemas import ProcessingHealthRead, ProcessingRunRead
from app.signals.models import DataSource, RawSignal

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def _make_source(session: Session, *, code: str, source_type: str) -> DataSource:
    source = DataSource(
        code=code,
        name=f"信源-{code}",
        source_type=source_type,
        credibility=80,
        enabled=True,
        schedule="*/30 * * * *",
        adapter_status="builtin",
        adapter_version=0,
        auth_type="none",
        login_config={},
        adapter_config={},
    )
    session.add(source)
    session.flush()
    return source


def _make_signal(
    session: Session, *, source: DataSource, fingerprint: str, collected_at: datetime
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=fingerprint,
        title=f"信号 {fingerprint}",
        content="用于队列拆解口径校验。",
        fingerprint=fingerprint,
        raw_data={},
        collected_at=collected_at,
        validity_state="pending_classification",
    )
    session.add(signal)
    session.flush()
    return signal


def test_queue_breakdown_counts_candidates_by_source_type(db_session: Session) -> None:
    official = _make_source(db_session, code="bd-official", source_type="official")
    media = _make_source(db_session, code="bd-media", source_type="media")
    _make_signal(
        db_session,
        source=official,
        fingerprint="bd-official-1",
        collected_at=NOW - timedelta(minutes=5),
    )
    _make_signal(
        db_session,
        source=media,
        fingerprint="bd-media-1",
        collected_at=NOW - timedelta(minutes=4),
    )
    _make_signal(
        db_session,
        source=media,
        fingerprint="bd-media-2",
        collected_at=NOW - timedelta(hours=3),
    )

    processing = build_monitoring_health(db_session, now=NOW).processing

    assert processing.total == 3
    assert processing.queue_by_source_type == {"official": 1, "media": 2}
    # 既有字段一个都不能少，且口径不变。
    assert processing.classification_failed == 0
    assert processing.backlog_over_1h == 1
    assert processing.oldest_pending_age_seconds == int(timedelta(hours=3).total_seconds())
    assert processing.last_run.status == "idle"


def test_queue_breakdown_excludes_non_candidates(db_session: Session) -> None:
    """停用信源、classification_failed 的信号不计入拆解（与 total 同口径）。"""
    active = _make_source(db_session, code="bd-active", source_type="official")
    disabled = _make_source(db_session, code="bd-disabled", source_type="official")
    disabled.enabled = False
    db_session.flush()
    _make_signal(
        db_session,
        source=active,
        fingerprint="bd-keep",
        collected_at=NOW - timedelta(minutes=1),
    )
    _make_signal(
        db_session,
        source=disabled,
        fingerprint="bd-drop-disabled",
        collected_at=NOW - timedelta(minutes=1),
    )
    failed = _make_signal(
        db_session,
        source=active,
        fingerprint="bd-drop-cf",
        collected_at=NOW - timedelta(minutes=1),
    )
    failed.validity_reason = {
        "code": "classification_failed",
        "anchor_source": "collected_at",
        "details": {},
    }
    db_session.flush()

    processing = build_monitoring_health(db_session, now=NOW).processing

    assert processing.total == 1
    assert processing.queue_by_source_type == {"official": 1}
    assert processing.classification_failed == 1


def test_processing_schema_keeps_old_payload_compatible() -> None:
    """旧响应（无 queue_by_source_type）仍可解析，新字段默认空字典。"""
    last_run = ProcessingRunRead(
        status="idle",
        started_at=None,
        finished_at=None,
        processed=0,
        filtered=0,
        failed=0,
    )
    legacy = ProcessingHealthRead(
        total=2,
        classification_failed=0,
        backlog_over_1h=0,
        oldest_pending_age_seconds=None,
        last_run=last_run,
    )
    assert legacy.queue_by_source_type == {}
