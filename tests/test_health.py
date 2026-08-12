from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_liveness_and_database_readiness() -> None:
    with TestClient(app) as client:
        live = client.get("/health")
        ready = client.get("/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
