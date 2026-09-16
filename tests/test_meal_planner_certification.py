from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User, UserPreference
from app.routers import llm_recommend
from app.services.meal_quality import SUPPORTED_ALLERGENS, SUPPORTED_DIETS, validate_meal

TARGET = {"kcal": 600.0, "protein_g": 35.0, "carbs_g": 78.0, "fat_g": 16.0}


def _safe_idea(slot: str, target: dict[str, float], suffix: str) -> dict:
    return {
        "title": f"Lentil Rice Bowl {slot} {suffix}",
        "ingredients": [
            {"name": "lentils", "amount": 180, "amount_g": 180, "unit": "g"},
            {"name": "brown rice", "amount": 220, "amount_g": 220, "unit": "g"},
            {"name": "broccoli", "amount": 100, "amount_g": 100, "unit": "g"},
            {"name": "olive oil", "amount": 12, "amount_g": 12, "unit": "g"},
        ],
        "instructions": [
            "Cook the lentils and brown rice until tender.",
            "Steam the broccoli, combine everything with olive oil, and serve.",
        ],
        "prep_time_min": 8,
        "cook_time_min": 20,
        "total_time_min": 28,
        "protein_group": "legume",
        "protein_item": "lentils",
        "carb_item": "brown rice",
        "approx_macros": dict(target),
    }


def _batch_result(days, *, expected_diet: str, expected_exclusions: list[str], **_kwargs):
    assert _kwargs["primary_diet"] == expected_diet
    assert set(expected_exclusions) <= set(_kwargs["exclusions"])
    output = {}
    for day_index, day in enumerate(days):
        slots = {}
        for meal_index, meal in enumerate(day["meals"]):
            target = meal["target_macros"]
            idea = _safe_idea(meal["slot"], target, f"{day_index}-{meal_index}")
            report = validate_meal(
                idea,
                target=target,
                diet=expected_diet,
                exclusions=expected_exclusions,
            )
            assert report.safe, (expected_diet, expected_exclusions, meal["slot"], report.codes())
            slots[meal["slot"]] = llm_recommend.SlotRecommendation(
                slot=meal["slot"],
                target=target,
                reason="Certified safe fixture.",
                meta={"provider": "certification", "mode": "create", "ai_idea": idea},
                ai_idea=idea,
            )
        output[day["date"]] = slots
    return output, {"mode": "certification", "accepted": sum(len(slots) for slots in output.values()), "rejected": 0}


def _request_day(day: date) -> llm_recommend.WeeklyDayRequest:
    return llm_recommend.WeeklyDayRequest(
        date=day.isoformat(),
        totals={
            "kcal": TARGET["kcal"] * 4,
            "protein_g": TARGET["protein_g"] * 4,
            "carbs_g": TARGET["carbs_g"] * 4,
            "fat_g": TARGET["fat_g"] * 4,
        },
        meals=[llm_recommend.MealTarget(slot=slot, **TARGET) for slot in llm_recommend.SLOTS],
    )


def test_daily_and_seven_day_workflows_complete_for_every_certification_profile(monkeypatch):
    """Every single allergen plus high-risk combinations use both planning paths."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(llm_recommend, "_get_openai_client", lambda: object())
    monkeypatch.setattr(llm_recommend._RATE, "check_and_add", lambda *_args: None)
    monkeypatch.setattr(llm_recommend._CACHE, "get", lambda *_args: None)
    monkeypatch.setattr(llm_recommend._CACHE, "set", lambda *_args: None)
    monkeypatch.setattr(llm_recommend, "feedback_context", lambda *_args: {"feedback_count": 0})
    monkeypatch.setattr(llm_recommend, "_get_week_used_recipe_ids", lambda *_args: set())
    monkeypatch.setattr(llm_recommend, "_get_week_used_meal_keys", lambda *_args: set())
    monkeypatch.setattr(llm_recommend, "_get_week_protein_counts", lambda *_args: {})
    monkeypatch.setattr(
        llm_recommend,
        "_persist_day_recommendations",
        lambda **kwargs: {
            "date": kwargs["day_iso"],
            "skipped": False,
            "applied": len(kwargs["day_items"]),
            "created_recipes": len(kwargs["day_items"]),
        },
    )

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    start = date(2026, 10, 5)
    completed_daily = 0
    completed_weekly_days = 0
    single_profiles = [(diet, [allergen]) for diet in SUPPORTED_DIETS for allergen in SUPPORTED_ALLERGENS]
    combined_exclusions = [
        ["milk", "egg"],
        ["peanut", "tree_nuts"],
        ["fish", "shellfish"],
        ["wheat", "soy", "sesame"],
        list(SUPPORTED_ALLERGENS),
    ]
    profiles = single_profiles + [(diet, exclusions) for diet in SUPPORTED_DIETS for exclusions in combined_exclusions]

    with Session(engine) as db:
        for profile_index, (diet, exclusions) in enumerate(profiles):
            user = User(
                email=f"cert-{profile_index}@example.com",
                password_hash="test",
                weight_kg=70,
                goal="maintain",
            )
            db.add(user)
            db.flush()
            db.add(
                UserPreference(
                    user_id=user.id,
                    diet_type=diet,
                    ingredient_exclusions="",
                    allergies=exclusions,
                    daily_snack_count=1,
                    snack_times=["15:00"],
                )
            )
            db.commit()

            def generator(
                _client,
                *,
                days,
                expected_diet=diet,
                expected_exclusions=tuple(exclusions),
                **kwargs,
            ):
                return _batch_result(
                    days,
                    expected_diet=expected_diet,
                    expected_exclusions=list(expected_exclusions),
                    **kwargs,
                )

            monkeypatch.setattr(llm_recommend, "_batch_week_recommendations", generator)
            monkeypatch.setattr(llm_recommend, "_parallel_week_recommendations", generator)

            daily_day = _request_day(start)
            daily = llm_recommend.recommend_recipes(
                request,
                llm_recommend.RecommendRequest(
                    date=daily_day.date,
                    totals=daily_day.totals,
                    meals=daily_day.meals,
                ),
                db,
                user,
            )
            assert len(daily["items"]) == 4
            assert all(item["ai_idea"] for item in daily["items"])
            completed_daily += 1

            week_days = [_request_day(start + timedelta(days=offset)) for offset in range(7)]
            weekly = llm_recommend.recommend_weekly_apply(
                request,
                llm_recommend.WeeklyRecommendRequest(days=week_days),
                db,
                user,
            )
            assert len(weekly["days"]) == 7
            assert all(len(day["items"]) == 4 for day in weekly["days"])
            completed_weekly_days += len(weekly["days"])

    assert completed_daily == len(profiles) == 56
    assert completed_weekly_days == len(profiles) * 7 == 392
