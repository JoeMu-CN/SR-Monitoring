import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.signals.models import CollectionRun, RawSignal


def signal_file(
    *, valid: bool = True, url: str = "https://example.com/risk/001"
) -> bytes:
    title = "华东地区强降雨预警" if valid else ""
    return json.dumps(
        {
            "version": "1.0",
            "signals": [
                {
                    "external_id": "MANUAL-20260805-001",
                    "title": title,
                    "content": "预计华东部分地区出现强降雨，交通可能受到影响。",
                    "url": url,
                    "published_at": "2026-08-05T08:00:00+08:00",
                },
                {
                    "external_id": "MANUAL-20260805-002",
                    "title": "港口临时管制",
                    "content": "受大风影响，部分港区临时停止装卸作业。",
                    "published_at": "2026-08-05T09:00:00+08:00",
                },
            ],
        },
        ensure_ascii=False,
    ).encode()


def upload(client: TestClient, content: bytes) -> object:
    return client.post(
        "/api/v1/signals/import",
        files={"file": ("signals.json", content, "application/json")},
    )


def test_signal_import_is_idempotent_and_runs_are_queryable(client: TestClient) -> None:
    first = upload(client, signal_file())
    second = upload(client, signal_file())

    assert first.status_code == 200
    assert first.json()["created_signals"] == 2
    assert first.json()["duplicate_signals"] == 0
    assert second.status_code == 200
    assert second.json()["created_signals"] == 0
    assert second.json()["duplicate_signals"] == 2

    sources = client.get("/api/v1/sources")
    assert sources.status_code == 200
    assert sources.json()[0]["code"] == "manual-json"

    runs = client.get("/api/v1/collection-runs", params={"status": "succeeded"})
    assert runs.status_code == 200
    assert runs.json()["total"] == 2


def test_invalid_signal_file_is_tracked_without_writes(
    client: TestClient, db_session: Session
) -> None:
    response = upload(client, signal_file(valid=False))

    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["path"] == "signals.0.title"
    assert db_session.scalar(select(func.count()).select_from(RawSignal)) == 0
    run = db_session.scalar(select(CollectionRun).order_by(CollectionRun.id.desc()))
    assert run is not None
    assert run.status == "failed"
    assert run.error is not None


def test_unsupported_url_scheme_is_rejected(client: TestClient) -> None:
    response = upload(client, signal_file(url="javascript:alert(1)"))

    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["path"] == "signals.0.url"
