from contextlib import contextmanager

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


@contextmanager
def _test_db():
    override = app.dependency_overrides[get_db]
    with contextmanager(override)() as db:
        yield db


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

        changed = client.patch(
            f"/v1/training-events/{event_id}",
            json={
                "workout_date": "2026-09-06",
                "start_time": None,
                "sport": "Rowing",
                "duration_min": 45,
                "intensity": "moderate",
                "distance_km": 10,
                "priority": "normal",
                "notes": "Technique session",
            },
        )
        assert changed.status_code == 200
        assert changed.json() == {
            **created.json(),
            "workout_date": "2026-09-06",
            "start_time": None,
            "sport": "Rowing",
            "duration_min": 45,
            "intensity": "moderate",
            "distance_km": 10.0,
            "priority": "normal",
            "notes": "Technique session",
        }

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
                "duration_min": 0,
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
        assert client.patch(f"/v1/training-events/{listed[0]['id']}", json={"duration_min": 30}).status_code == 409
        assert client.delete(f"/v1/training-events/{listed[0]['id']}").status_code == 204
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


def test_completed_strava_activity_is_removed_from_upcoming_and_context():
    from datetime import UTC, datetime, timedelta

    with _client() as client:
        client.post("/auth/signup", json={"email": "reconcile@example.com", "password": "a-secure-password-123"})
        started = datetime.now(UTC) - timedelta(hours=1)
        day = started.date().isoformat()
        created = client.post(
            "/v1/training-events",
            json={"workout_date": day, "sport": "Strength", "duration_min": 75, "intensity": "moderate"},
        )
        assert created.status_code == 201

        override = app.dependency_overrides[get_db]
        db_iterator = override()
        db = next(db_iterator)
        try:
            from app.models import User

            user = db.query(User).filter(User.email == "reconcile@example.com").one()
            db.add(
                Activity(
                    user_id=user.id,
                    provider="strava",
                    source_provider="strava",
                    source_id="completed-strength",
                    start_time=started.replace(tzinfo=None),
                    duration_s=89 * 60,
                    kcal=628,
                    sport="WeightTraining",
                )
            )
            db.commit()
        finally:
            db_iterator.close()

        listed = client.get(f"/v1/training-events?from={day}&to={day}")
        assert listed.status_code == 200
        assert listed.json()["items"] == []

        context = client.get(f"/v1/training-events/context/{day}?days=1")
        assert context.status_code == 200
        assert context.json()["upcoming_planned"] == 0
        assert context.json()["completed_plan_matches"] == 1
    app.dependency_overrides.clear()


def test_manual_duration_accepts_exact_minutes_and_short_sessions():
    with _client() as client:
        client.post("/auth/signup", json={"email": "minutes@example.com", "password": "a-secure-password-123"})
        created = client.post(
            "/v1/training-events",
            json={"workout_date": "2026-10-26", "sport": "Run", "duration_min": 36, "intensity": "easy"},
        )
        assert created.status_code == 201
        assert created.json()["duration_min"] == 36
        event_id = created.json()["id"]
        for minutes in (1, 7, 37, 1440):
            changed = client.patch(f"/v1/training-events/{event_id}", json={"duration_min": minutes})
            assert changed.status_code == 200
            assert changed.json()["duration_min"] == minutes
        for minutes in (0, -1, 1441, 36.5):
            assert client.patch(f"/v1/training-events/{event_id}", json={"duration_min": minutes}).status_code == 422
    app.dependency_overrides.clear()


def test_moving_early_workout_updates_only_affected_unlocked_meals():
    from datetime import UTC, date, datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from app.models import User

    with _client() as client:
        client.post("/auth/signup", json={"email": "cross-day@example.com", "password": "a-secure-password-123"})
        with _test_db() as db:
            user = db.query(User).filter(User.email == "cross-day@example.com").one()
            user.weight_kg = 70
            user.timezone = "America/Los_Angeles"
            db.commit()

        tz = ZoneInfo("America/Los_Angeles")
        first = datetime.now(tz).date() + timedelta(days=3)
        days = [first + timedelta(days=offset) for offset in range(5)]

        def get_plan(day: date, *, create: bool = False) -> dict:
            response = client.get(f"/v1/plan/{day}?create_if_missing={str(create).lower()}")
            assert response.status_code == 200
            return response.json()

        before = {day: get_plan(day, create=True) for day in days}
        client.post(f"/v1/plan/{days[3]}/lock")
        locked = get_plan(days[3])
        started = datetime.combine(days[1], time(7), tz).astimezone(UTC)
        created = client.post(
            "/v1/training-events",
            json={
                "workout_date": days[1].isoformat(),
                "start_time": started.isoformat(),
                "sport": "Ride",
                "intensity": "hard",
                "duration_min": 120,
            },
        )
        assert created.status_code == 201
        after = get_plan(days[0])
        changes = [
            meal["meal_type"]
            for meal, original in zip(after["meals"], before[days[0]]["meals"], strict=True)
            if meal["carbs_g"] != original["carbs_g"]
        ]
        assert changes == ["dinner"]
        dinner = next(meal for meal in after["meals"] if meal["meal_type"] == "dinner")
        assert dinner["meta"]["training_carb_change"] == {
            "grams": 35,
            "workouts": [
                {
                    "reason": "before_early_workout",
                    "sport": "Ride",
                    "date": days[1].isoformat(),
                    "start_time": started.astimezone(tz).isoformat(),
                }
            ],
        }
        assert all("training_carb_change" not in meal["meta"] for meal in after["meals"] if meal is not dinner)

        moved = client.patch(f'/v1/training-events/{created.json()["id"]}', json={"workout_date": days[3].isoformat()})
        assert moved.status_code == 200
        moved_start = datetime.fromisoformat(moved.json()["start_time"])
        assert moved_start.astimezone(tz).hour == 7
        restored = get_plan(days[0])
        assert restored["totals"] == before[days[0]]["totals"]
        restored_dinner = next(meal for meal in restored["meals"] if meal["meal_type"] == "dinner")
        assert restored_dinner["meta"]["training_carb_change"] == {
            **dinner["meta"]["training_carb_change"],
            "grams": -35,
        }
        assert get_plan(days[2])["totals"]["carbs_g"] > before[days[2]]["totals"]["carbs_g"]
        assert get_plan(days[3]) == locked
        assert get_plan(days[4]) == before[days[4]]
    app.dependency_overrides.clear()


def test_workout_fuel_can_be_logged_retried_and_cleared():
    from datetime import UTC, datetime, time

    from app.models import User

    with _client() as client:
        client.post("/auth/signup", json={"email": "fuel@example.com", "password": "a-secure-password-123"})
        now = datetime.now(UTC).replace(tzinfo=None)
        day = now.date()
        with _test_db() as db:
            user = db.query(User).filter(User.email == "fuel@example.com").one()
            user.weight_kg = 70
            activity = Activity(user_id=user.id, start_time=datetime.combine(day, time.min), duration_s=0, sport="Ride")
            db.add(activity)
            db.commit()
            activity_id = activity.id

        endpoint = f"/v1/plan/{day}"
        before = client.get(f"{endpoint}?create_if_missing=true").json()
        log_url = f"/activities/{activity_id}/fuel"
        assert client.put(log_url, json={"carbs_g": -5}).status_code == 422
        result = client.put(log_url, json={"carbs_g": 60})
        assert result.status_code == 200
        assert result.json()["meal_plan_update"]["adjusted_meals"] == 4
        after = client.get(endpoint).json()
        assert after["workout_fuel"] == {"carbs_g": 60, "kcal": 240}
        assert abs(after["totals"]["carbs_g"] - (before["totals"]["carbs_g"] - 60)) < 1
        assert abs(after["totals"]["kcal"] - (before["totals"]["kcal"] - 240)) < 1
        for meal, original in zip(after["meals"], before["meals"], strict=True):
            change = meal["meta"]["training_carb_change"]
            assert change["grams"] == round(meal["carbs_g"] - original["carbs_g"], 1) < 0
            assert [(row["reason"], row["sport"]) for row in change["workouts"]] == [("logged_fuel", "Ride")]

        assert client.put(log_url, json={"carbs_g": 60}).json()["meal_plan_update"]["adjusted_meals"] == 0
        assert client.get(endpoint).json() == after

        assert client.put(log_url, json={"carbs_g": None}).status_code == 200
        assert abs(client.get(endpoint).json()["totals"]["carbs_g"] - before["totals"]["carbs_g"]) < 1
        client.post("/auth/logout")
        client.post("/auth/signup", json={"email": "other-fuel@example.com", "password": "a-secure-password-123"})
        assert client.put(log_url, json={"carbs_g": 60}).status_code == 404
    app.dependency_overrides.clear()


def test_workout_reversal_restores_exact_saved_meal_and_ingredient_portions():
    from datetime import UTC, datetime, timedelta

    from app.models import User

    with _client() as client:
        email = "restore-portions@example.com"
        client.post("/auth/signup", json={"email": email, "password": "a-secure-password-123"})
        with _test_db() as db:
            db.query(User).filter(User.email == email).one().weight_kg = 70
            db.commit()

        day = datetime.now(UTC).date() + timedelta(days=3)
        path = f"/v1/plan/{day}"
        rows = (
            ("Cooked white rice", 150.25, 195.2, 4.1, 42.2, 0.4),
            ("Cooked chicken breast", 150.5, 250.4, 46.1, 0, 5.4),
            ("Olive oil", 10.25, 92.3, 0, 0, 10.25),
        )
        ingredients = [
            {
                "name": name,
                "qty": qty,
                "unit": "g",
                "kcal": kcal,
                "protein_g": protein,
                "carbs_g": carbs,
                "fat_g": fat,
                "meta": {"nutrition_basis": "usda_fooddata_central"},
            }
            for name, qty, kcal, protein, carbs, fat in rows
        ]
        workout = client.post(
            "/v1/training-events",
            json={"workout_date": str(day), "sport": "Ride", "duration_min": 75, "intensity": "easy"},
        )
        assert workout.status_code == 201
        workout_path = f"/v1/training-events/{workout.json()['id']}"
        existing = client.post(
            path,
            json={
                "source": "llm",
                "meals": [
                    {
                        "meal_type": slot,
                        "title": title,
                        "kcal": 537.9,
                        "protein_g": 50.2,
                        "carbs_g": 42.2,
                        "fat_g": 16.05,
                        "ingredients": ingredients,
                    }
                    for slot, title in (
                        ("breakfast", "Rice and Chicken Breakfast"),
                        ("lunch", "Rice and Chicken Lunch"),
                        ("snack", "Rice and Chicken Snack"),
                    )
                ],
            },
        )
        assert existing.status_code == 200
        original = client.get(path).json()
        for _ in range(2):
            hard = client.patch(workout_path, json={"duration_min": 120, "intensity": "hard"})
            assert hard.json()["meal_plan_update"]["adjusted_meals"] == 3
            fitted = client.get(path).json()["meals"]
            assert all(meal["carbs_g"] > 42.2 for meal in fitted)
            easy = client.patch(workout_path, json={"duration_min": 75, "intensity": "easy"})
            assert easy.json()["meal_plan_update"]["adjusted_meals"] == 3
            restored = client.get(path).json()
            assert restored["totals"] == original["totals"]
            for before, after, hard_meal in zip(original["meals"], restored["meals"], fitted, strict=True):
                assert after["ingredients"] == before["ingredients"]
                for key in ("carbs_g", "kcal", "protein_g", "fat_g"):
                    assert after[key] == before[key]
                assert after["meta"]["training_carb_change"]["grams"] == round(
                    before["carbs_g"] - hard_meal["carbs_g"], 1
                )
    app.dependency_overrides.clear()
