from dataclasses import asdict
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import Plan, PlanMeal, PlannedWorkout, User, UserPreference
from app.routers import llm_recommend
from app.services.meal_quality import MACROS, SUPPORTED_ALLERGENS, text_violates_exclusions, validate_meal
from app.services.training_nutrition import athlete_profile_baseline


@pytest.mark.parametrize("exclusions", [("soy",), SUPPORTED_ALLERGENS], ids=["soy-free", "all-nine"])
def test_weekly_planner_saves_all_verified_vegan_meals(monkeypatch, exclusions):
    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", True)
    monkeypatch.setattr(llm_recommend, "_get_openai_client", lambda: None)
    monkeypatch.setattr(llm_recommend._RATE, "check_and_add", lambda *_args: None)

    def unexpected_generation(*args, **kwargs):
        pytest.fail("A complete verified week should not need model generation")

    monkeypatch.setattr(llm_recommend, "_parallel_week_recommendations", unexpected_generation)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    week = date(2026, 11, 16)
    slots = ("breakfast", "lunch", "dinner", "snack", "snack_2")

    with Session(engine) as db:
        user = User(
            email="vegan@example.invalid", password_hash="test", weight_kg=80, height_cm=180, dob=date(1988, 5, 1)
        )
        db.add(user)
        db.flush()
        db.add(UserPreference(user_id=user.id, diet_type="vegan", allergies=list(exclusions), daily_snack_count=2))
        db.add(PlannedWorkout(user_id=user.id, workout_date=week, sport="cycling", duration_min=135, intensity="hard"))
        db.commit()
        totals = asdict(athlete_profile_baseline(user, week))
        targets = [llm_recommend.MealTarget(slot=slot, **{k: v / 5 for k, v in totals.items()}) for slot in slots]
        payload = llm_recommend.WeeklyRecommendRequest(
            days=[
                llm_recommend.WeeklyDayRequest(
                    date=(week + timedelta(days=i)).isoformat(), totals=totals, meals=targets
                )
                for i in range(7)
            ]
        )
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        result = llm_recommend.recommend_weekly_apply(request, payload, db, user)
        assert result["generation"]["mode"] == "prevalidated_vegan"
        assert all(day["applied"] == 5 for day in result["persist"]["applied"])
        assert sum(item["target"]["kcal"] for item in result["days"][0]["items"]) > totals["kcal"]

        saved = db.scalars(select(PlanMeal).join(Plan).where(Plan.user_id == user.id)).all()
        assert len(saved) == len({meal.title for meal in saved}) == 35
        for meal in saved:
            ingredients = [
                {
                    "name": item.name,
                    "amount": item.qty,
                    "unit": item.unit,
                    "nutrition": {k: getattr(item, k) for k in MACROS},
                }
                for item in meal.items
            ]
            assert all(item.meta["nutrition_basis"] == "usda_fooddata_central" for item in meal.items)
            report = validate_meal(
                {
                    "title": meal.title,
                    "slot": meal.meal_type,
                    "ingredients": ingredients,
                    "instructions": meal.instructions.splitlines(),
                    "macros": {k: getattr(meal, k) for k in MACROS},
                    **{k: meal.meta[k] for k in ("prep_time_min", "cook_time_min", "total_time_min")},
                },
                diet="vegan",
                exclusions=list(exclusions),
                require_ingredient_nutrition=True,
            )
            assert report.safe, (meal.title, report.codes())


def test_vegan_options_decline_additional_restrictions(monkeypatch):
    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", True)
    target = {"kcal": 600, "protein_g": 35, "carbs_g": 70, "fat_g": 20}
    days = [{"date": "2026-11-16", "meals": [{"slot": "breakfast", "target_macros": target}]}]

    assert llm_recommend._prevalidated_vegan_week(days, ["soy"])
    assert not llm_recommend._prevalidated_vegan_week(days, ["soy", "sunflower seed"])
    assert text_violates_exclusions("sunflower seed flour mixed with wheat flour", ["wheat"]) == ["wheat"]
    assert not text_violates_exclusions("sunflower seed flour mixed with chickpea flour", ["wheat"])
