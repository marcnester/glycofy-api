from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Activity


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


def test_trainingpeaks_csv_preview_and_idempotent_import():
    csv_text = """WorkoutDay,WorkoutType,WorkoutTitle,PlannedDuration,DistancePlanned,PlannedDistanceUnit,IntensityFactor,StartTimePlanned
2099-09-10,Bike,Long Ride,2.0,60,km,.90,2099-09-10T15:00:00Z
2099-09-11,Run,Recovery Run,0.5,5,km,.60,
"""
    upload = {"file": ("workouts.csv", csv_text, "text/csv")}
    with _client() as client:
        client.post("/auth/signup", json={"email": "csv@example.com", "password": "a-secure-password-123"})
        preview = client.post("/v1/training-events/import/trainingpeaks", files=upload)
        assert preview.status_code == 200
        assert preview.json()["valid"] == 2
        assert preview.json()["confirmed"] is False
        assert preview.json()["preview"][0]["duration_min"] == 120
        assert preview.json()["preview"][0]["intensity"] == "hard"

        empty = client.get("/v1/training-events?from=2099-09-10&to=2099-09-11")
        assert empty.json()["items"] == []

        first = client.post("/v1/training-events/import/trainingpeaks?confirm=true", files=upload)
        assert first.status_code == 200
        assert first.json()["imported"] == 2

        second = client.post("/v1/training-events/import/trainingpeaks?confirm=true", files=upload)
        assert second.json()["imported"] == 0
        assert second.json()["unchanged"] == 2

        listed = client.get("/v1/training-events?from=2099-09-10&to=2099-09-11").json()["items"]
        assert {item["source"] for item in listed} == {"trainingpeaks_csv"}
        assert client.delete(f'/v1/training-events/{listed[0]["id"]}').status_code == 204
    app.dependency_overrides.clear()


def test_training_context_explains_standard_and_complete_modes():
    with _client() as client:
        client.post("/auth/signup", json={"email": "context@example.com", "password": "a-secure-password-123"})
        standard = client.get("/v1/training-events/context/2099-09-10")
        assert standard.status_code == 200
        assert standard.json()["state"] == "standard"
        assert "standard carbohydrate" in standard.json()["message"]

        client.post(
            "/v1/training-events",
            json={"workout_date": "2099-09-10", "sport": "Ride", "duration_min": 60, "intensity": "moderate"},
        )
        planned_only = client.get("/v1/training-events/context/2099-09-10").json()
        assert planned_only["state"] == "missing_recent"

        override = app.dependency_overrides[get_db]
        db_iterator = override()
        db = next(db_iterator)
        try:
            from datetime import UTC, datetime

            from app.models import User

            user = db.query(User).filter(User.email == "context@example.com").one()
            db.add(Activity(user_id=user.id, start_time=datetime.now(UTC).replace(tzinfo=None), sport="Ride"))
            db.commit()
        finally:
            db_iterator.close()
        complete = client.get("/v1/training-events/context/2099-09-10").json()
        assert complete["state"] == "complete"
        assert complete["recent_completed"] == 1
        assert complete["upcoming_planned"] == 1
    app.dependency_overrides.clear()
