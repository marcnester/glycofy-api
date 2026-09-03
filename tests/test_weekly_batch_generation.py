import json
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User, WeeklyPlanningJob
from app.routers import llm_recommend


def _meal(slot: str, day: int) -> dict:
    protein = f"protein-{day}-{slot}"
    carb = f"carb-{day}-{slot}"
    dish = {"breakfast": "oat bowl", "lunch": "grain salad", "dinner": "vegetable skillet", "snack": "fruit parfait"}[
        slot
    ]
    return {
        "slot": slot,
        "title": f"Day {day} {dish}",
        "ingredients": [
            {"name": protein, "amount": "150", "unit": "g"},
            {"name": carb, "amount": "1", "unit": "cup"},
            {"name": "spinach", "amount": "2", "unit": "cups"},
            {"name": "olive oil", "amount": "1", "unit": "tbsp"},
        ],
        "instructions": ["Cook the protein and carbohydrate.", "Combine and serve."],
        "prep_time_min": 10,
        "cook_time_min": 15,
        "total_time_min": 25,
        "protein_group": "plant" if day == 1 else "poultry",
        "protein_item": protein,
        "carb_item": carb,
        "macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15},
        "reason": "Balanced for the target.",
    }


def test_weekly_targets_rebalance_a_malformed_prior_meal_split():
    day = llm_recommend.WeeklyDayRequest(
        date="2026-09-01",
        totals={"kcal": 2400, "protein_g": 180, "carbs_g": 240, "fat_g": 80},
        meals=[
            llm_recommend.MealTarget(
                slot=slot,
                kcal=600,
                protein_g=45,
                carbs_g=2 if slot == "breakfast" else 79.33,
                fat_g=20,
            )
            for slot in llm_recommend.SLOTS
        ],
    )

    targets = {meal.slot: meal for meal in llm_recommend._balanced_weekly_targets(day)}

    assert targets["breakfast"].carbs_g == 60
    assert targets["lunch"].carbs_g == 72
    assert targets["dinner"].carbs_g == 72
    assert targets["snack"].carbs_g == 36
    assert sum(meal.carbs_g for meal in targets.values()) == 240


def test_weekly_batch_uses_one_structured_call_and_accepts_complete_week(monkeypatch):
    dates = ["2026-09-01", "2026-09-02"]
    response_body = {
        "days": [
            {"date": date, "meals": [_meal(slot, day) for slot in llm_recommend.SLOTS]}
            for day, date in enumerate(dates, start=1)
        ]
    }
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_body)))],
                usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 200}),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    days = [
        {
            "date": date,
            "training": {},
            "diet_tags": [],
            "meals": [
                {
                    "slot": slot,
                    "target_macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15},
                }
                for slot in llm_recommend.SLOTS
            ],
        }
        for date in dates
    ]

    monkeypatch.setattr(llm_recommend, "_circuit_open", lambda: False)
    monkeypatch.setattr(llm_recommend, "_daily_budget_usd", lambda: 100.0)
    recommendations, meta = llm_recommend._batch_week_recommendations(
        client,
        days=days,
        primary_diet="omnivore",
        diet_tags=[],
        exclusions=[],
        athlete_feedback={"feedback_count": 3, "favorite_meals": ["Salmon rice bowl"]},
    )

    assert len(calls) == 1
    assert calls[0]["response_format"]["type"] == "json_schema"
    sent_payload = json.loads(calls[0]["messages"][1]["content"])
    assert sent_payload["athlete_feedback"]["favorite_meals"] == ["Salmon rice bowl"]
    assert meta["accepted"] == 8
    assert meta["rejected"] == 0
    assert all(set(recommendations[date]) == set(llm_recommend.SLOTS) for date in dates)
    assert all(item.meta["batch"] is True for slots in recommendations.values() for item in slots.values())
    assert all(item.ai_idea["total_time_min"] == 25 for slots in recommendations.values() for item in slots.values())


def test_weekly_job_status_is_scoped_to_owner():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    job_id = "owner-scoped-job"
    with Session(engine) as db:
        db.add_all(
            [
                User(id=42, email="owner@example.com", password_hash="x"),
                User(id=99, email="stranger@example.com", password_hash="x"),
            ]
        )
        db.add(
            WeeklyPlanningJob(
                id=job_id,
                user_id=42,
                status="completed",
                stage="completed",
                message="Ready",
                completed_days=7,
                total_days=7,
                payload={},
                result={"days": []},
                cancel_requested=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
        )
        db.commit()
        owner = SimpleNamespace(id=42)
        response = llm_recommend.weekly_job_status(job_id, db, owner)
        assert response.status == "completed"

        stranger = SimpleNamespace(id=99)
        try:
            llm_recommend.weekly_job_status(job_id, db, stranger)
        except llm_recommend.HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("another user could read the weekly job")


def test_weekly_job_can_be_cancelled_by_its_owner():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(id=42, email="owner@example.com", password_hash="x")
        db.add(user)
        db.add(
            WeeklyPlanningJob(
                id="cancel-me",
                user_id=42,
                status="running",
                stage="generating",
                message="Working",
                completed_days=0,
                total_days=7,
                payload={},
                cancel_requested=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        db.commit()

        response = llm_recommend.cancel_weekly_job("cancel-me", db, user)
        assert response.status == "running"
        job = db.get(WeeklyPlanningJob, "cancel-me")
        assert job is not None and job.cancel_requested is True


def test_weekly_batch_rejects_a_meal_with_bad_macros(monkeypatch):
    date = "2026-09-01"
    meals = [_meal(slot, 1) for slot in llm_recommend.SLOTS]
    meals[0]["macros"]["carbs_g"] = 2
    response_body = {"days": [{"date": date, "meals": meals}]}

    class Completions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_body)))],
                usage=None,
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    days = [
        {
            "date": date,
            "training": {},
            "diet_tags": [],
            "meals": [
                {
                    "slot": slot,
                    "target_macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15},
                }
                for slot in llm_recommend.SLOTS
            ],
        }
    ]
    monkeypatch.setattr(llm_recommend, "_circuit_open", lambda: False)
    monkeypatch.setattr(llm_recommend, "_daily_budget_usd", lambda: 100.0)

    recommendations, meta = llm_recommend._batch_week_recommendations(
        client,
        days=days,
        primary_diet="omnivore",
        diet_tags=[],
        exclusions=[],
    )

    assert "breakfast" not in recommendations[date]
    assert meta["accepted"] == 3
    assert meta["rejected"] == 1
