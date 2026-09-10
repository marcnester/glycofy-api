from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    Activity,
    EnergyTarget,
    GroceryApproval,
    GroceryPreference,
    MealFeedback,
    Plan,
    PlanItem,
    PlanMeal,
    PlannedWorkout,
    User,
    UserPreference,
    WeeklyPlanningJob,
)

PRIVATE_DAY = date(2099, 9, 9)


@pytest.fixture()
def authorization_app():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    owner = TestClient(app)
    stranger = TestClient(app)
    try:
        assert (
            owner.post(
                "/auth/signup", json={"email": "matrix-owner@example.com", "password": "owner-password-123"}
            ).status_code
            == 200
        )
        assert (
            stranger.post(
                "/auth/signup", json={"email": "matrix-stranger@example.com", "password": "stranger-password-123"}
            ).status_code
            == 200
        )
        yield owner, stranger, engine
    finally:
        owner.close()
        stranger.close()
        app.dependency_overrides.clear()


def _seed_owner_records(engine) -> dict[str, int | str]:
    now = datetime.utcnow()
    with Session(engine) as db:
        owner = db.query(User).filter_by(email="matrix-owner@example.com").one()
        plan = Plan(user_id=owner.id, date=PRIVATE_DAY, locked=False, totals={"kcal": 700}, source="llm")
        meal = PlanMeal(
            meal_type="dinner",
            title="Owner private dinner",
            kcal=700,
            protein_g=50,
            carbs_g=80,
            fat_g=20,
            order_index=3,
            tags=[],
            meta={},
            created_at=now,
            updated_at=now,
        )
        meal.items.append(PlanItem(name="private ingredient", qty=1, unit="item", meta={}))
        plan.meals.append(meal)
        db.add(plan)
        db.flush()
        db.add(
            MealFeedback(
                user_id=owner.id,
                plan_meal_id=meal.id,
                plan_date=PRIVATE_DAY,
                meal_type="dinner",
                meal_title="Owner private dinner",
                outcome="eaten",
                note="private feedback",
                created_at=now,
                updated_at=now,
            )
        )
        workout = PlannedWorkout(
            user_id=owner.id,
            workout_date=PRIVATE_DAY,
            sport="Ride",
            duration_min=60,
            intensity="moderate",
            priority="normal",
            source="manual",
        )
        db.add(workout)
        db.add(
            Activity(
                user_id=owner.id,
                provider="strava",
                source_id="owner-private-activity",
                sport="Ride",
                start_time=datetime(2099, 9, 9, 8),
            )
        )
        db.add(
            EnergyTarget(
                user_id=owner.id,
                date=PRIVATE_DAY,
                target_kcal=2700,
                meta={"private": "owner"},
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            UserPreference(
                user_id=owner.id,
                diet_type="pescatarian",
                ingredient_exclusions="owner-secret-food",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            GroceryPreference(
                user_id=owner.id,
                ingredient_key="private ingredient",
                preferred_brand="Owner Brand",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            GroceryApproval(
                user_id=owner.id,
                start_date=PRIVATE_DAY,
                end_date=PRIVATE_DAY,
                servings=1,
                items=[{"name": "private ingredient"}],
                plan_fingerprint=[],
                approved_at=now,
                updated_at=now,
            )
        )
        job = WeeklyPlanningJob(
            id="owner-private-job",
            user_id=owner.id,
            status="failed",
            stage="failed",
            message="Owner-only failure",
            completed_days=0,
            total_days=1,
            payload={"days": []},
            error="private job detail",
            cancel_requested=False,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        db.add(job)
        db.commit()
        return {"meal_id": meal.id, "workout_id": workout.id, "owner_id": owner.id, "job_id": job.id}


def test_object_id_endpoints_conceal_another_users_records(authorization_app):
    owner, stranger, engine = authorization_app
    ids = _seed_owner_records(engine)

    attempts = [
        stranger.get(f'/v1/feedback/meals/{ids["meal_id"]}'),
        stranger.put(f'/v1/feedback/meals/{ids["meal_id"]}', json={"outcome": "skipped"}),
        stranger.delete(f'/v1/feedback/meals/{ids["meal_id"]}'),
        stranger.patch(f'/v1/training-events/{ids["workout_id"]}', json={"duration_min": 30}),
        stranger.delete(f'/v1/training-events/{ids["workout_id"]}'),
        stranger.get(f'/v1/llm/recommend/weekly/jobs/{ids["job_id"]}'),
        stranger.post(f'/v1/llm/recommend/weekly/jobs/{ids["job_id"]}/retry'),
        stranger.post(f'/v1/llm/recommend/weekly/jobs/{ids["job_id"]}/cancel'),
    ]

    assert [response.status_code for response in attempts] == [404] * len(attempts)
    assert owner.get(f'/v1/feedback/meals/{ids["meal_id"]}').status_code == 200
    assert owner.get(f'/v1/llm/recommend/weekly/jobs/{ids["job_id"]}').status_code == 200


def test_date_keyed_records_and_collections_are_account_isolated(authorization_app):
    owner, stranger, engine = authorization_app
    ids = _seed_owner_records(engine)
    day = PRIVATE_DAY.isoformat()

    assert stranger.get(f"/v1/plan/{day}").status_code == 404
    assert stranger.patch(f"/v1/plan/{day}", json={"locked": True}).status_code == 404
    assert stranger.post(f"/v1/plan/{day}/lock").status_code == 404
    assert stranger.post(f"/v1/plan/{day}/regenerate").status_code == 404
    assert stranger.post(f'/v1/plan/{day}/meals/{ids["meal_id"]}/assign_recipe/1').status_code == 404

    energy = stranger.get(f"/v1/energy/{day}")
    assert energy.status_code == 200
    assert energy.json()["user_id"] != ids["owner_id"]
    assert energy.json()["target_kcal"] is None
    assert energy.json()["meta"] == {}

    assert stranger.get(f"/v1/training-events?from={day}&to={day}").json()["items"] == []
    assert stranger.get("/activities?from=2099-09-09&to=2099-09-09").json()["items"] == []
    assert "owner-private-activity" not in stranger.get("/activities/csv?from=2099-09-09&to=2099-09-09").text
    assert stranger.get("/v1/feedback/insights").json()["feedback_count"] == 0
    assert stranger.get("/v1/llm/recommend/weekly/jobs").json() is None

    preferences = stranger.get("/v1/preferences").json()
    assert preferences["diet"] != "pescatarian"
    assert "owner-secret-food" not in str(preferences)
    grocery = stranger.get("/v1/plan/grocery-list/preferences").json()
    assert grocery["preferences"] == []
    approval = stranger.get(f"/v1/plan/grocery-list/approval?start={day}&end={day}").json()
    assert approval["approval"] is None
    weekly_list = stranger.get(f"/v1/plan/grocery-list/week?start={day}&end={day}").json()
    assert weekly_list["plan_count"] == 0
    assert weekly_list["items"] == []


def test_same_date_writes_create_only_the_callers_records(authorization_app):
    owner, stranger, engine = authorization_app
    ids = _seed_owner_records(engine)
    day = PRIVATE_DAY.isoformat()

    created = stranger.post(f"/v1/plan/{day}", json={"totals": {"kcal": 1800}, "meals": []})
    assert created.status_code == 200
    assert (
        stranger.put(f"/v1/energy/{day}", json={"target_kcal": 1800, "meta": {"scope": "stranger"}}).status_code == 200
    )
    assert (
        stranger.put(
            "/v1/plan/grocery-list/preferences",
            json={"ingredient_key": "private ingredient", "in_pantry": True},
        ).status_code
        == 200
    )

    owner_plan = owner.get(f"/v1/plan/{day}").json()
    owner_energy = owner.get(f"/v1/energy/{day}").json()
    owner_grocery = owner.get("/v1/plan/grocery-list/preferences").json()["preferences"]
    assert owner_plan["totals"] == {"kcal": 700.0, "protein_g": 50.0, "carbs_g": 80.0, "fat_g": 20.0}
    assert owner_plan["meals"][0]["title"] == "Owner private dinner"
    assert owner_energy["target_kcal"] == 2700
    assert owner_energy["meta"] == {"private": "owner"}
    assert owner_grocery[0]["preferred_brand"] == "Owner Brand"
    with Session(engine) as db:
        stranger_user = db.query(User).filter_by(email="matrix-stranger@example.com").one()
        stranger_plan = db.query(Plan).filter_by(user_id=stranger_user.id, date=PRIVATE_DAY).one()
        assert stranger_plan.user_id != ids["owner_id"]


def test_self_service_and_admin_surfaces_do_not_cross_accounts(authorization_app):
    owner, stranger, engine = authorization_app
    _seed_owner_records(engine)

    exported = stranger.get("/users/me/export")
    assert exported.status_code == 200
    export_text = exported.text
    assert "matrix-owner@example.com" not in export_text
    assert "Owner private dinner" not in export_text
    assert "owner-secret-food" not in export_text

    for path in (
        "/v1/operations/ai-summary",
        "/v1/operations/nutrition-source-health",
        "/v1/operations/beta-summary",
        "/v1/operations/feedback",
        "/v1/operations/failed-jobs",
    ):
        assert stranger.get(path).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/users/me"),
        ("get", "/users/me/export"),
        ("get", "/activities"),
        ("get", "/v1/plan/2099-09-09"),
        ("get", "/v1/energy/2099-09-09"),
        ("get", "/v1/preferences"),
        ("get", "/v1/training-events?from=2099-09-09&to=2099-09-09"),
        ("get", "/v1/feedback/insights"),
        ("get", "/v1/llm/recommend/weekly/jobs"),
    ],
)
def test_private_surfaces_require_authentication(method: str, path: str):
    with TestClient(app) as client:
        assert getattr(client, method)(path).status_code == 401
