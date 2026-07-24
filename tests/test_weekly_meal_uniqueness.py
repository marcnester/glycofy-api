from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Plan, PlanMeal, User
from app.routers import llm_recommend


def test_ai_created_meal_retries_duplicate_weekly_title(monkeypatch):
    responses = iter(
        [
            (
                {
                    "mode": "create",
                    "new_recipe": {
                        "title": "  Savory Quinoa and Spinach Bowl  ",
                        "ingredients": ["quinoa", "spinach", "salmon"],
                        "instructions": ["Cook and serve."],
                        "protein_group": "fish",
                        "protein_item": "salmon",
                        "carb_item": "quinoa",
                        "macro_estimate": {"kcal": 500, "protein_g": 40, "carbs_g": 55, "fat_g": 14},
                    },
                    "reason": "first attempt",
                },
                {},
            ),
            (
                {
                    "mode": "create",
                    "new_recipe": {
                        "title": "Smoked Salmon Sweet Potato Hash",
                        "ingredients": ["sweet potato", "spinach", "salmon"],
                        "instructions": ["Cook and serve."],
                        "protein_group": "fish",
                        "protein_item": "salmon",
                        "carb_item": "sweet potato",
                        "macro_estimate": {"kcal": 510, "protein_g": 41, "carbs_g": 57, "fat_g": 14},
                    },
                    "reason": "unique retry",
                },
                {},
            ),
        ]
    )
    monkeypatch.setattr(llm_recommend, "_safe_openai_json_pick", lambda *_args, **_kwargs: next(responses))

    mode, recipe, _deltas, _reason, meta, ai_idea = llm_recommend._llm_pick_or_create(
        client=object(),
        slot="breakfast",
        tgt=llm_recommend.MealTarget(slot="breakfast", kcal=500, protein_g=40, carbs_g=55, fat_g=14),
        candidates=[],
        date="2026-07-25",
        diet_tags=None,
        primary_diet="omnivore",
        user_pref=None,
        used_protein_items=[],
        used_carb_items=[],
        used_recipe_ids=set(),
        used_meal_keys={llm_recommend._meal_similarity_key("savory quinoa and spinach bowl")},
        allow_new_recipe=True,
    )

    assert mode == "create"
    assert recipe is None
    assert ai_idea is not None
    assert ai_idea["title"] == "Smoked Salmon Sweet Potato Hash"
    assert meta["retry"] == "day_variety"


def test_meal_similarity_ignores_word_order_and_presentation_words():
    assert llm_recommend._meal_similarity_key("Cottage Cheese and Berry Parfait") == llm_recommend._meal_similarity_key(
        "Cottage Cheese Berry Delight"
    )
    assert llm_recommend._meal_similarity_key("Savory Chickpea Quinoa Bowl") == llm_recommend._meal_similarity_key(
        "Quinoa Chickpea Breakfast Bowl"
    )


def test_single_day_generation_loads_titles_from_other_days_this_week():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user = User(email="athlete@example.com", password_hash="test")
        monday = Plan(user=user, date=date(2026, 7, 27))
        monday.meals.append(
            PlanMeal(
                meal_type="dinner",
                title="Lemon Garlic Shrimp with Sweet Potato and Spinach",
                order_index=2,
            )
        )
        db.add(user)
        db.commit()

        used_titles = llm_recommend._get_week_used_meal_keys(db, user, date(2026, 7, 28))

    assert used_titles == {llm_recommend._meal_similarity_key("Lemon Garlic Shrimp with Sweet Potato and Spinach")}


def test_cache_key_changes_when_weekly_meal_history_changes():
    request = llm_recommend.RecommendRequest(
        date="2026-07-28",
        meals=[llm_recommend.MealTarget(slot="dinner", kcal=670, protein_g=64, carbs_g=70, fat_g=16)],
    )

    before = llm_recommend._cache_key(1, request, [], set())
    after = llm_recommend._cache_key(
        1,
        request,
        [],
        {llm_recommend._meal_similarity_key("Lemon Garlic Pasta with Scallops and Spinach")},
    )

    assert before != after
