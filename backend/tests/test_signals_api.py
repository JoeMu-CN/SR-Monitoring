"""采集记录读取 API：生命周期与处理状态双字段契约。

覆盖：
- ``processing_state`` 始终随信号返回，旧客户端可继续读取 ``validity_state``；
- 生命周期未定时 ``validity_state`` 允许为 null；
- ``scope=valid`` 只把 active + (pending_classification|analyzed) 计入有效证据。
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.signals.models import DataSource, RawSignal

NOW = datetime.now(UTC)


def _source(session: Session, code: str) -> DataSource:
    source = DataSource(
        code=code,
        name=f"测试信源 {code}",
        source_type="official_api",
        credibility=90,
        auth_type="none",
        login_config={},
        adapter_config={},
        adapter_status="builtin",
        adapter_version=0,
        enabled=True,
    )
    session.add(source)
    session.flush()
    return source


def _signal(
    session: Session,
    source: DataSource,
    index: int,
    *,
    validity_state: str | None,
    processing_state: str,
) -> RawSignal:
    """构造一条带完整 active 策略快照的采集记录；处理状态显式写入。"""
    signal = RawSignal(
        source_id=source.id,
        external_id=f"signal-{index}",
        title=f"采集记录 {index}",
        content=f"采集记录正文 {index}",
        fingerprint=f"fingerprint-{source.code}-{index}",
        raw_data={},
        collected_at=NOW - timedelta(minutes=index),
        validity_profile="transport_disruption" if validity_state == "active" else None,
        validity_state=validity_state,
        processing_state=processing_state,
        valid_from=NOW - timedelta(days=1) if validity_state == "active" else None,
        valid_until=NOW + timedelta(days=30) if validity_state == "active" else None,
        validity_mode="fixed_days" if validity_state == "active" else None,
        validity_policy_version="v1" if validity_state == "active" else None,
        validity_reason={
            "code": "active" if validity_state == "active" else "pending_classification",
            "anchor_source": "published_at" if validity_state == "active" else "collected_at",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    if validity_state is None:
        # validity_state 列仍有 server_default：ORM 构造函数传 None 会被默认值覆盖，
        # 这里显式 UPDATE 成 SQL NULL（与写入侧 Core insert 的 NULL 语义一致）。
        session.execute(
            update(RawSignal)
            .where(RawSignal.id == signal.id)
            .values(validity_state=None)
        )
        session.expire(signal)
    session.flush()
    return signal


def test_signal_list_exposes_processing_state_and_nullable_validity(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "api-lifecycle-source")
    analyzed = _signal(
        db_session,
        source,
        1,
        validity_state="active",
        processing_state="analyzed",
    )
    no_trigger = _signal(
        db_session,
        source,
        2,
        validity_state=None,
        processing_state="no_trigger",
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals?scope=all")

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[analyzed.id]["processing_state"] == "analyzed"
    assert items[analyzed.id]["validity_state"] == "active"
    assert items[no_trigger.id]["processing_state"] == "no_trigger"
    assert items[no_trigger.id]["validity_state"] is None


def test_signal_detail_exposes_processing_state(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "api-detail-source")
    signal = _signal(
        db_session,
        source,
        1,
        validity_state="active",
        processing_state="pending_classification",
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals/{signal.id}")

    assert response.status_code == 200
    assert response.json()["processing_state"] == "pending_classification"


def test_valid_scope_only_counts_active_and_processable_signals(
    client: TestClient, db_session: Session
) -> None:
    source = _source(db_session, "api-valid-scope-source")
    keep_pending = _signal(
        db_session,
        source,
        1,
        validity_state="active",
        processing_state="pending_classification",
    )
    keep_analyzed = _signal(
        db_session,
        source,
        2,
        validity_state="active",
        processing_state="analyzed",
    )
    _signal(
        db_session,
        source,
        3,
        validity_state="active",
        processing_state="filtered",
    )
    _signal(
        db_session,
        source,
        4,
        validity_state="active",
        processing_state="no_trigger",
    )
    _signal(
        db_session,
        source,
        5,
        validity_state="active",
        processing_state="classification_failed",
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals?scope=valid")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {item["id"] for item in payload["items"]} == {
        keep_pending.id,
        keep_analyzed.id,
    }
