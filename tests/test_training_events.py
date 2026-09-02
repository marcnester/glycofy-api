from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_manual_training_event_crud_is_owner_scoped():
    with _client() as client:
        client.post("/auth/signup", json={"email": "one@example.com", "password": "a-secure-password-123"})
        created = client.post(
            "/v1/training-events",
            json={
                "workout_date": "2026-09-05",
                "start_time": "2026-09-05T14:00:00Z",
                "sport": "Ride",
                "duration_min": 120,
                "intensity": "hard",
                "distance_km": 60,
                "priority": "key",
                "notes": "Long ride",
            },
        )
        assert created.status_code == 201
        event_id = created.json()["id"]
        assert created.json()["source"] == "manual"

        listed = client.get("/v1/training-events?from=2026-09-01&to=2026-09-07")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [event_id]

        changed = client.patch(f"/v1/training-events/{event_id}", json={"duration_min": 150})
        assert changed.status_code == 200
        assert changed.json()["duration_min"] == 150

        client.post("/auth/logout")
        client.post("/auth/signup", json={"email": "two@example.com", "password": "a-secure-password-123"})
        assert client.patch(f"/v1/training-events/{event_id}", json={"duration_min": 30}).status_code == 404
        assert client.delete(f"/v1/training-events/{event_id}").status_code == 404
    app.dependency_overrides.clear()


def test_training_event_rejects_invalid_duration_and_range():
    with _client() as client:
        client.post("/auth/signup", json={"email": "range@example.com", "password": "a-secure-password-123"})
        invalid = client.post(
            "/v1/training-events",
            json={
                "workout_date": "2026-09-05",
                "sport": "Run",
                "duration_min": 2,
                "intensity": "hard",
            },
        )
        assert invalid.status_code == 422
        assert client.get("/v1/training-events?from=2026-01-01&to=2026-09-07").status_code == 400
    app.dependency_overrides.clear()
