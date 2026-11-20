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
        audit = after["nutrition_targets"]
        assert audit["dinner_carbs_g"] == 35
        assert audit["cross_day_sessions"] == dinner["meta"]["training_carb_change"]["workouts"]
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
        locked_after = get_plan(days[3])
        assert locked_after.pop("nutrition_targets")["training"]["planned_workout_count"] == 1
        assert locked.pop("nutrition_targets")["training"]["planned_workout_count"] == 0
        assert locked_after == locked
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
        audit = after["nutrition_targets"]
        assert audit["workout_fuel_credit_g"] == 60
        assert audit["meal_targets"]["carbs_g"] == audit["final"]["carbs_g"] - 60
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


def test_reviewed_fueling_portion_requires_approval_and_restores_exactly():
    from datetime import UTC, datetime, time, timedelta

    from app.models import User

    with _client() as client:
        email = "review-fueling@example.com"
        assert client.post("/auth/signup", json={"email": email, "password": "a-secure-password-123"}).is_success
        with _test_db() as db:
            user = db.query(User).filter_by(email=email).one()
            user.weight_kg = 70
            user.timezone = "UTC"
            db.commit()

        dinner_day = datetime.now(UTC).date() + timedelta(days=3)
        ride_day = dinner_day + timedelta(days=1)
        ride = {
            "workout_date": str(ride_day),
            "start_time": datetime.combine(ride_day, time(7), UTC).isoformat(),
            "sport": "Ride",
            "duration_min": 75,
            "intensity": "easy",
        }
        created = client.post("/v1/training-events", json=ride)
        assert created.status_code == 201
        ride_path = f"/v1/training-events/{created.json()['id']}"
        path = f"/v1/plan/{dinner_day}"
        assert client.post(
            path,
            json={
                "source": "llm",
                "meals": [
                    {
                        "meal_type": "dinner",
                        "title": "Chicken and rice",
                        "instructions": "Serve the warmed chicken with cooked rice.",
                        "kcal": 540,
                        "protein_g": 48,
                        "carbs_g": 56.3,
                        "fat_g": 13,
                        "ingredients": [
                            {
                                "name": "Cooked white rice",
                                "qty": 200.25,
                                "unit": "g",
                                "kcal": 260,
                                "protein_g": 5.4,
                                "carbs_g": 56.3,
                                "fat_g": 0.6,
                                "meta": {"nutrition_basis": "usda_fooddata_central"},
                            },
                            {"name": "Chicken breast", "qty": 150, "unit": "g"},
                        ],
                    }
                ],
            },
        ).is_success
        original = client.get(path).json()["meals"][0]
        assert client.patch(ride_path, json={"duration_min": 120, "intensity": "hard"}).is_success
        reviewed = client.get(path).json()["meals"][0]
        assert reviewed["ingredients"] == original["ingredients"]
        assert reviewed["carbs_g"] == original["carbs_g"]
        preview = reviewed["fueling_adjustment"]
        assert preview["ingredient"] == "Cooked white rice"
        assert preview["change_carbs_g"] == 35
        assert preview["proposed_carbs_g"] == 91.3
        endpoint = f"{path}/meals/{reviewed['id']}/fueling-adjustment"
        approval = {"token": preview["token"]}
        assert client.post(f"{path}/lock").is_success
        locked = client.get(path).json()["meals"][0]
        assert "fueling_adjustment" not in locked
        assert client.post(endpoint, json=approval).status_code == 409
        assert client.get(path).json()["meals"][0] == locked
        assert client.post(f"{path}/lock?lock=false").is_success

        assert client.post(endpoint, json={"token": "0" * 64}).status_code == 409
        assert client.post(endpoint, json=approval).is_success
        applied = client.get(path).json()["meals"][0]
        assert applied["carbs_g"] == preview["proposed_carbs_g"]
        assert applied["ingredients"][0]["qty"] == preview["proposed_qty_g"]
        assert applied["ingredients"][1] == original["ingredients"][1]
        assert applied["meta"]["training_carb_change"]["workouts"][0]["reason"] == "before_early_workout"
        assert client.post(endpoint, json=approval).status_code == 409

        assert client.patch(ride_path, json={"duration_min": 75, "intensity": "easy"}).is_success
        restored = client.get(path).json()["meals"][0]
        assert restored["ingredients"] == original["ingredients"]
        assert {key: restored[key] for key in ("kcal", "protein_g", "carbs_g", "fat_g")} == {
            key: original[key] for key in ("kcal", "protein_g", "carbs_g", "fat_g")
        }
        assert restored["meta"]["training_carb_change"]["grams"] == -35
        assert "fueling_adjustment" not in restored

        # Leave an earlier training review unapproved before making the
        # second ride edit, as can happen on a real weekly plan.
        assert (
            client.post(
                "/v1/training-events",
                json={"workout_date": str(dinner_day), "sport": "Ride", "duration_min": 90, "intensity": "hard"},
            ).status_code
            == 201
        )
        pending = client.get(path).json()["meals"][0]
        assert pending["meta"]["training_replan"] == "review"
        assert pending["ingredients"] == original["ingredients"]
        assert client.patch(ride_path, json={"duration_min": 120, "intensity": "hard"}).is_success
        next_preview = client.get(path).json()["meals"][0]["fueling_adjustment"]
        assert client.post(endpoint, json={"token": next_preview["token"]}).is_success
        assert client.patch(ride_path, json={"duration_min": 75, "intensity": "easy"}).is_success
        restored_again = client.get(path).json()["meals"][0]
        assert restored_again["ingredients"] == pending["ingredients"]
        assert restored_again["carbs_g"] == pending["carbs_g"]
        assert restored_again["meta"]["training_replan_target"] == pending["meta"]["training_replan_target"]
    app.dependency_overrides.clear()


def test_fueling_side_reuses_grocery_item_and_disappears_on_reversal():
    from datetime import UTC, datetime, time, timedelta

    from app.models import User

    with _client() as client:
        email = "fueling-side@example.com"
        assert client.post("/auth/signup", json={"email": email, "password": "a-secure-password-123"}).is_success
        with _test_db() as db:
            user = db.query(User).filter_by(email=email).one()
            user.weight_kg = 70
            user.timezone = "UTC"
            db.commit()

        day = datetime.now(UTC).date() + timedelta(days=3)
        next_day = day + timedelta(days=1)
        path = f"/v1/plan/{day}"
        ride = client.post(
            "/v1/training-events",
            json={
                "workout_date": str(next_day),
                "start_time": datetime.combine(next_day, time(7), UTC).isoformat(),
                "sport": "Ride",
                "duration_min": 75,
                "intensity": "easy",
            },
        )
        assert ride.status_code == 201
        ride_path = f"/v1/training-events/{ride.json()['id']}"
        assert client.post(
            path,
            json={
                "source": "llm",
                "meals": [
                    {
                        "meal_type": "dinner",
                        "title": "Chicken and rice",
                        "instructions": "Serve the chicken with 180 g of cooked rice.",
                        "kcal": 540,
                        "protein_g": 48,
                        "carbs_g": 56.3,
                        "fat_g": 13,
                        "ingredients": [{"name": "Chicken breast", "qty": 150, "unit": "g"}],
                    }
                ],
            },
        ).is_success
        donor_path = f"/v1/plan/{next_day}"
        assert client.post(
            donor_path,
            json={
                "source": "llm",
                "meals": [
                    {
                        "meal_type": "snack",
                        "title": "Banana",
                        "kcal": 89,
                        "protein_g": 1.1,
                        "carbs_g": 23,
                        "fat_g": 0.3,
                        "ingredients": [
                            {
                                "name": "Banana",
                                "qty": 100,
                                "unit": "g",
                                "kcal": 89,
                                "protein_g": 1.1,
                                "carbs_g": 23,
                                "fat_g": 0.3,
                                "meta": {"nutrition_basis": "usda_fooddata_central"},
                            }
                        ],
                    }
                ],
            },
        ).is_success
        assert client.post(f"{donor_path}/lock").is_success
        donor = client.get(donor_path).json()["meals"]
        original = client.get(path).json()["meals"][0]
        grocery_url = f"/v1/plan/grocery-list/week?start={day}"
        assert client.put(
            "/v1/plan/grocery-list/preferences",
            json={"ingredient_key": "banana", "in_pantry": True, "package_quantity": 100, "package_unit": "g"},
        ).is_success
        original_groceries = client.get(grocery_url).json()["items"]
        assert client.patch(ride_path, json={"duration_min": 120, "intensity": "hard"}).is_success
        pending = client.get(path).json()["meals"][0]
        preview = pending["fueling_adjustment"]
        assert preview["kind"] == "side"
        assert preview["ingredient"] == "Banana"
        assert preview["change_carbs_g"] == 35
        shopping = preview["shopping_impact"]
        assert shopping["quantity_change_g"] == preview["proposed_qty_g"]
        assert shopping["in_pantry"] is True
        assert shopping["start"] == str(day)
        assert shopping["end"] == str(day + timedelta(days=6))
        assert shopping["after"]["quantity"] > shopping["before"]["quantity"]
        assert shopping["package_change"]["after"] >= shopping["package_change"]["before"]
        preference_path = "/v1/plan/grocery-list/preferences"
        stock_payload = {
            "ingredient_key": "banana",
            "in_pantry": True,
            "package_quantity": 100,
            "package_unit": "g",
            "pantry_quantity": 50,
            "purchased_quantity": 100,
            "stock_unit": "g",
        }
        assert client.put(preference_path, json=stock_payload).is_success
        coverage = client.get(path).json()["meals"][0]["fueling_adjustment"]["shopping_impact"]["stock_coverage"]
        assert coverage["status"] == "short"
        assert coverage["available"] == 150
        assert coverage["buy_quantity"] == 102.2
        assert coverage["packages_to_buy"] == 2
        assert client.put(preference_path, json={**stock_payload, "purchased_quantity": 250}).is_success
        coverage = client.get(path).json()["meals"][0]["fueling_adjustment"]["shopping_impact"]["stock_coverage"]
        assert coverage["status"] == "enough"
        assert coverage["buy_quantity"] == 0
        # Convert ounces to grams, and preserve stock on legacy preference saves.
        assert client.put(
            preference_path, json={**stock_payload, "pantry_quantity": 1, "purchased_quantity": 1, "stock_unit": "oz"}
        ).is_success
        assert client.put(preference_path, json={"ingredient_key": "banana", "in_pantry": True}).is_success
        saved = client.get("/v1/plan/grocery-list/preferences").json()["preferences"][0]
        assert saved["stock_unit"] == "oz"
        assert saved["purchased_quantity"] == 1
        coverage = client.get(path).json()["meals"][0]["fueling_adjustment"]["shopping_impact"]["stock_coverage"]
        assert coverage["status"] == "short"
        assert coverage["buy_quantity"] > 0
        assert client.put(preference_path, json={**stock_payload, "stock_unit": "item"}).is_success
        coverage = client.get(path).json()["meals"][0]["fueling_adjustment"]["shopping_impact"]["stock_coverage"]
        assert coverage["status"] == "incompatible_units"
        assert client.put(preference_path, json={**stock_payload, "pantry_quantity": -1}).status_code == 422
        assert client.put(preference_path, json={"ingredient_key": "banana", "pantry_quantity": 20}).status_code == 422
        assert client.put(
            preference_path,
            json={**stock_payload, "pantry_quantity": None, "purchased_quantity": None, "stock_unit": None},
        ).is_success
        original_groceries = client.get(grocery_url).json()["items"]

        assert pending["ingredients"] == original["ingredients"]
        endpoint = f"{path}/meals/{pending['id']}/fueling-adjustment"
        approval = {"token": preview["token"]}
        assert client.post(f"{path}/lock").is_success
        assert client.post(endpoint, json=approval).status_code == 409
        assert client.get(path).json()["meals"][0]["ingredients"] == original["ingredients"]
        assert client.post(f"{path}/lock?lock=false").is_success
        assert client.post(endpoint, json=approval).is_success
        applied = client.get(path).json()["meals"][0]
        assert applied["ingredients"][:-1] == original["ingredients"]
        side = applied["ingredients"][-1]
        assert side["meta"]["fueling_side"] is True
        assert side["qty"] == preview["proposed_qty_g"]
        assert applied["carbs_g"] == preview["proposed_carbs_g"]
        assert applied["instructions"] == original["instructions"]
        assert client.get(grocery_url).json()["items"] != original_groceries
        assert client.post(endpoint, json=approval).status_code == 409
        assert client.patch(ride_path, json={"duration_min": 75, "intensity": "easy"}).is_success
        restored = client.get(path).json()["meals"][0]
        assert restored["ingredients"] == original["ingredients"]
        for macro in ("carbs_g", "protein_g", "fat_g", "kcal"):
            assert restored[macro] == original[macro]
        assert restored["meta"]["training_carb_change"]["grams"] == -35
        assert client.get(grocery_url).json()["items"] == original_groceries
        assert client.get(donor_path).json()["meals"] == donor
    app.dependency_overrides.clear()
