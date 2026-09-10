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
            {
                "name": protein,
                "amount": "150",
                "unit": "g",
                "nutrition": {"kcal": 220, "protein_g": 30, "carbs_g": 10, "fat_g": 6},
            },
            {
                "name": carb,
                "amount": "1",
                "unit": "cup",
                "nutrition": {"kcal": 200, "protein_g": 6, "carbs_g": 35, "fat_g": 4},
            },
            {
                "name": "spinach",
                "amount": "2",
                "unit": "cups",
                "nutrition": {"kcal": 30, "protein_g": 4, "carbs_g": 5, "fat_g": 0},
            },
            {
                "name": "olive oil",
                "amount": "1",
                "unit": "tbsp",
                "nutrition": {"kcal": 50, "protein_g": 0, "carbs_g": 0, "fat_g": 5},
            },
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


def test_preferred_snacks_split_existing_daily_targets_without_adding_calories():
    day = llm_recommend.WeeklyDayRequest(
        date="2026-09-01",
        totals={"kcal": 2400, "protein_g": 180, "carbs_g": 300, "fat_g": 80},
        meals=[
            llm_recommend.MealTarget(slot=slot, kcal=600, protein_g=45, carbs_g=75, fat_g=20)
            for slot in llm_recommend.SLOTS
        ],
    )
    pref = SimpleNamespace(daily_snack_count=2, snack_times=["10:00", "15:00"])

    targets = llm_recommend._targets_for_preferences(day, pref)

    assert [target.slot for target in targets] == ["breakfast", "lunch", "dinner", "snack", "snack_2"]
    assert sum(target.kcal for target in targets) == 2400
    assert targets[-2].kcal == targets[-1].kcal == 180
    assert llm_recommend._snack_schedule(pref) == [("snack", "10:00"), ("snack_2", "15:00")]


def test_weekly_batch_parallelizes_bounded_day_calls_and_accepts_complete_week(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-luna")
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
    recommendations, meta = llm_recommend._parallel_week_recommendations(
        client,
        days=days,
        primary_diet="omnivore",
        diet_tags=[],
        exclusions=[],
        athlete_feedback={"feedback_count": 3, "favorite_meals": ["Salmon rice bowl"]},
    )

    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["model"] == "gpt-5.6-luna"
    assert calls[0]["reasoning_effort"] == "none"
    assert "temperature" not in calls[0]
    assert "max_completion_tokens" in calls[0]
    assert "max_tokens" not in calls[0]
    sent_payload = json.loads(calls[0]["messages"][1]["content"])
    assert sent_payload["athlete_feedback"]["favorite_meals"] == ["Salmon rice bowl"]
    assert len(sent_payload["days"]) == 1
    assert len(sent_payload["week_context"]) == 2
    assert sent_payload["days"][0]["variety_assignment"] in llm_recommend._DAY_THEMES
    system_prompt = calls[0]["messages"][0]["content"]
    assert "never copy target_macros into macros" in system_prompt
    ingredient_schema = calls[0]["response_format"]["json_schema"]["schema"]["properties"]["days"]["items"][
        "properties"
    ]["meals"]["items"]["properties"]["ingredients"]["items"]
    assert "nutrition" not in ingredient_schema["properties"]
    assert "Do not calculate or return nutrition for individual ingredients" in system_prompt
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


def test_weekly_job_status_includes_the_planned_date_range():
    job = WeeklyPlanningJob(
        id="dated-job",
        user_id=42,
        status="completed",
        stage="completed",
        message="Ready",
        completed_days=2,
        total_days=2,
        payload={"days": [{"date": "2026-09-03"}, {"date": "2026-09-04"}]},
        cancel_requested=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    result = llm_recommend._weekly_job_dict(job)
    assert result["start_date"] == "2026-09-03"
    assert result["end_date"] == "2026-09-04"


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


def test_weekly_batch_rejects_macros_not_supported_by_ingredient_sum(monkeypatch):
    date = "2026-09-01"
    meals = [_meal(slot, 1) for slot in llm_recommend.SLOTS]
    meals[0]["ingredients"][0]["nutrition"]["protein_g"] = 2
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
                {"slot": slot, "target_macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15}}
                for slot in llm_recommend.SLOTS
            ],
        }
    ]
    monkeypatch.setattr(llm_recommend, "_circuit_open", lambda: False)
    monkeypatch.setattr(llm_recommend, "_daily_budget_usd", lambda: 100.0)

    recommendations, meta = llm_recommend._batch_week_recommendations(
        client, days=days, primary_diet="omnivore", diet_tags=[], exclusions=[]
    )

    assert "breakfast" not in recommendations[date]
    assert meta["rejected"] == 1
