"""信息源采集记录只读清单契约测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.permissions import PERM_SOURCE_STATUS_VIEW, ROLE_PERMISSIONS
from app.signals.models import DataSource, RawSignal


def _source(session: Session, code: str, validity_days: int | None) -> DataSource:
    source = DataSource(
        code=code,
        name=f"测试信息源 {code}",
        source_type="official_api",
        credibility=90,
        auth_type="none",
        login_config={},
        adapter_config={},
        adapter_status="builtin",
        adapter_version=0,
        enabled=True,
        signal_validity_days=validity_days,
    )
    session.add(source)
    session.flush()
    return source


def _signal(
    session: Session,
    source: DataSource,
    index: int,
    *,
    published_at: datetime | None,
    collected_at: datetime,
) -> RawSignal:
    signal = RawSignal(
        source_id=source.id,
        external_id=f"external-{index}",
        title=f"采集记录 {index}",
        content=f"采集记录正文 {index}",
        url=f"https://example.test/signals/{index}",
        published_at=published_at,
        collected_at=collected_at,
        fingerprint=f"fingerprint-{source.code}-{index}",
        raw_data={"secret": index},
        validity_state="legacy",
        validity_reason={
            "code": "legacy_unmigrated",
            "anchor_source": "legacy",
            "details": {},
        },
    )
    session.add(signal)
    session.flush()
    return signal


def test_source_signals_requires_session_and_permission(
    client: TestClient,
    db_session: Session,
    auth_as,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(db_session, "permission-source", None)
    db_session.commit()

    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.get(f"/api/v1/sources/{source.id}/signals").status_code == 401

    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        "viewer",
        ROLE_PERMISSIONS["viewer"] - {PERM_SOURCE_STATUS_VIEW},
    )
    auth_as("viewer", "source-signals-no-permissions")
    assert client.get(f"/api/v1/sources/{source.id}/signals").status_code == 403


@pytest.mark.parametrize("role", ["viewer", "risk_analyst", "risk_admin", "platform_admin"])
def test_source_signals_is_readable_by_all_formal_roles(
    client: TestClient,
    db_session: Session,
    auth_as,
    role: str,
) -> None:
    source = _source(db_session, f"role-source-{role}", None)
    db_session.commit()
    auth_as(role, f"source-signals-{role}")

    response = client.get(f"/api/v1/sources/{source.id}/signals")

    assert response.status_code == 200
    assert response.json()["source"] == {
        "id": source.id,
        "code": source.code,
        "name": source.name,
        "signal_validity_days": None,
    }


def test_source_signals_filters_valid_records_with_null_published_fallback(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    source = _source(db_session, "validity-source", 10)
    permanent = _source(db_session, "permanent-source", None)
    valid_published = _signal(
        db_session,
        source,
        1,
        published_at=now - timedelta(days=2),
        collected_at=now - timedelta(days=1),
    )
    valid_collected = _signal(
        db_session,
        source,
        2,
        published_at=None,
        collected_at=now - timedelta(days=3),
    )
    _signal(
        db_session,
        source,
        3,
        published_at=now - timedelta(days=20),
        collected_at=now,
    )
    _signal(
        db_session,
        source,
        4,
        published_at=None,
        collected_at=now - timedelta(days=20),
    )
    _signal(
        db_session,
        permanent,
        5,
        published_at=now - timedelta(days=200),
        collected_at=now - timedelta(days=200),
    )
    db_session.commit()

    response = client.get(f"/api/v1/sources/{source.id}/signals?scope=valid&offset=0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == 20
    assert payload["offset"] == 0
    assert {item["id"] for item in payload["items"]} == {
        valid_published.id,
        valid_collected.id,
    }
    assert all("raw_data" not in item and "fingerprint" not in item for item in payload["items"])


def test_source_signals_all_scope_is_stably_paginated_and_source_isolated(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    source = _source(db_session, "paged-source", None)
    other = _source(db_session, "other-source", None)
    expected: list[int] = []
    for index in range(23):
        signal = _signal(
            db_session,
            source,
            index,
            published_at=now - timedelta(hours=index // 2),
            collected_at=now - timedelta(minutes=index),
        )
        expected.append(signal.id)
    _signal(db_session, other, 99, published_at=now, collected_at=now)
    db_session.commit()

    first = client.get(f"/api/v1/sources/{source.id}/signals?scope=all&offset=0")
    second = client.get(f"/api/v1/sources/{source.id}/signals?scope=all&offset=20")
    overflow = client.get(f"/api/v1/sources/{source.id}/signals?scope=all&offset=40")

    assert first.status_code == second.status_code == overflow.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["total"] == second_payload["total"] == overflow.json()["total"] == 23
    # 未传 limit 时保持既有默认 20，兼容历史调用方。
    assert first_payload["limit"] == second_payload["limit"] == 20
    returned = [item["id"] for item in first_payload["items"] + second_payload["items"]]
    assert returned == expected
    assert len(returned) == len(set(returned)) == 23
    assert overflow.json()["items"] == []


def test_source_signals_orders_by_collected_at_then_id_desc_ignoring_published_at(
    client: TestClient,
    db_session: Session,
) -> None:
    """采集时间必须压过发布时间定序，同采集时间按 id 倒序，分页无重无漏。"""
    now = datetime.now(UTC)
    source = _source(db_session, "order-source", None)
    # published_at 与 collected_at 顺序刻意相反：发布时间最新者采集时间最早，
    # 采集时间最新者发布时间最旧；中间两条采集时间相同，只能靠 id 倒序定序。
    newest_collected = _signal(
        db_session,
        source,
        1,
        published_at=now - timedelta(days=30),
        collected_at=now,
    )
    tie_lower_id = _signal(
        db_session,
        source,
        2,
        published_at=now,
        collected_at=now - timedelta(hours=1),
    )
    tie_higher_id = _signal(
        db_session,
        source,
        3,
        published_at=None,
        collected_at=now - timedelta(hours=1),
    )
    stale_collected = _signal(
        db_session,
        source,
        4,
        published_at=now,
        collected_at=now - timedelta(hours=3),
    )
    db_session.commit()

    first = client.get(f"/api/v1/sources/{source.id}/signals?scope=all&limit=2&offset=0")
    second = client.get(f"/api/v1/sources/{source.id}/signals?scope=all&limit=2&offset=2")

    assert first.status_code == second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["total"] == second_payload["total"] == 4
    assert [item["id"] for item in first_payload["items"]] == [
        newest_collected.id,
        tie_higher_id.id,
    ]
    assert [item["id"] for item in second_payload["items"]] == [
        tie_lower_id.id,
        stale_collected.id,
    ]
    returned = [item["id"] for item in first_payload["items"] + second_payload["items"]]
    assert len(returned) == len(set(returned)) == 4


def test_source_signals_explicit_limit_returns_hundred_rows_and_offset_100_has_no_gap(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    source = _source(db_session, "hundred-source", None)
    expected: list[int] = []
    for index in range(150):
        signal = _signal(
            db_session,
            source,
            index,
            published_at=now - timedelta(hours=index // 2),
            collected_at=now - timedelta(minutes=index),
        )
        expected.append(signal.id)
    db_session.commit()

    first = client.get(f"/api/v1/sources/{source.id}/signals?scope=all&limit=100&offset=0")
    second = client.get(
        f"/api/v1/sources/{source.id}/signals?scope=all&limit=100&offset=100"
    )

    assert first.status_code == second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["limit"] == second_payload["limit"] == 100
    assert first_payload["total"] == second_payload["total"] == 150
    assert len(first_payload["items"]) == 100
    assert len(second_payload["items"]) == 50
    returned = [item["id"] for item in first_payload["items"] + second_payload["items"]]
    assert returned == expected
    assert len(returned) == len(set(returned)) == 150


def test_source_signals_rejects_limit_below_one_or_above_hundred(
    client: TestClient,
) -> None:
    assert client.get("/api/v1/sources/1/signals?limit=101").status_code == 422
    assert client.get("/api/v1/sources/1/signals?limit=0").status_code == 422


def test_source_signals_returns_404_and_rejects_invalid_query(
    client: TestClient,
) -> None:
    assert client.get("/api/v1/sources/999999999/signals").status_code == 404
    assert client.get("/api/v1/sources/1/signals?scope=recent").status_code == 422
    assert client.get("/api/v1/sources/1/signals?offset=-1").status_code == 422
