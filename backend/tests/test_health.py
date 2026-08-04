from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok_when_database_is_available(monkeypatch) -> None:
    monkeypatch.setattr("app.main.check_database", lambda: None)

    response = client.get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_is_unavailable(monkeypatch) -> None:
    def fail() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.main.check_database", fail)

    response = client.get("/api/v1/system/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
