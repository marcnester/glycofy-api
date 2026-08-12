from datetime import date
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Plan, PlanItem, PlanMeal, Recipe, User
from app.routers import llm_recommend


def test_ai_created_meal_retries_duplicate_weekly_title(monkeypatch):
    responses = iter(
        [
            (
                {
                    "mode": "create",
                    "new_recipe": {
                        "title": "  Savory Quinoa and Spinach Bowl  ",
                        "ingredients": [
                            {"name": "quinoa", "amount": "1", "unit": "cup"},
                            {"name": "spinach", "amount": "2", "unit": "cups"},
                            {"name": "salmon", "amount": "6", "unit": "oz"},
                        ],
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
                        "ingredients": [
                            {"name": "sweet potato", "amount": "8", "unit": "oz"},
                            {"name": "spinach", "amount": "2", "unit": "cups"},
                            {"name": "salmon", "amount": "6", "unit": "oz"},
                        ],
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


def test_weekly_recipe_apply_replaces_placeholders_with_real_ingredients():
    meal = PlanMeal(meal_type="breakfast", title="Breakfast", order_index=0)
    meal.items.extend(
        [
            PlanItem(name="Protein (lean)", meta={}),
            PlanItem(name="Complex carbs", meta={}),
            PlanItem(name="Fats (healthy)", meta={}),
        ]
    )
    recipe = Recipe(
        id=42,
        title="Greek Yogurt Berry Bowl",
        meal_type="breakfast",
        kcal=480,
        protein_g=38,
        carbs_g=52,
        fat_g=14,
        ingredients=[
            {"name": "Greek yogurt", "qty": 1, "unit": "cup"},
            {"name": "Mixed berries", "quantity": "1/2", "unit": "cup"},
            "chia seeds",
        ],
    )

    llm_recommend._apply_recipe_to_planmeal(meal, recipe)

    assert [item.name for item in meal.items] == ["Greek yogurt", "Mixed berries", "chia seeds"]
    assert meal.items[0].qty == 1
    assert meal.items[0].unit == "cup"


def test_generated_ingredients_require_amounts_and_units():
    assert llm_recommend._ingredients_have_quantities(
        [
            {"name": "tuna", "amount": "1", "unit": "can"},
            {"name": "sweet potato", "amount": "8", "unit": "oz"},
            {"name": "spinach", "amount": "2", "unit": "cups"},
        ]
    )
    assert llm_recommend._ingredients_have_quantities(["1 cup quinoa", "6 oz salmon"])
    assert not llm_recommend._ingredients_have_quantities(["tuna", "sweet potato"])
    assert not llm_recommend._ingredients_have_quantities([{"name": "spinach", "amount": "2", "unit": ""}])


def test_weekly_fast_path_uses_complete_catalog_recipe_without_llm(monkeypatch):
    recipe = MagicMock()
    recipe.id = 9
    recipe.title = "Measured Salmon Bowl"
    recipe.meal_type = "lunch"
    recipe.kcal = 650
    recipe.protein_g = 42
    recipe.carbs_g = 70
    recipe.fat_g = 18
    recipe.ingredients = ["6 oz salmon", "1 cup cooked rice", "1 cup spinach"]
    recipe.instructions = "Cook and assemble."
    recipe.protein_group = "fish"

    monkeypatch.setattr(
        llm_recommend,
        "_top_k_candidates",
        lambda **_kwargs: [(recipe, {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}, 0.0)],
    )
    monkeypatch.setattr(
        llm_recommend,
        "_llm_pick_or_create",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    recommendation = llm_recommend._recommend_for_single_meal(
        client=object(),
        db=MagicMock(),
        date="2026-08-06",
        tgt=llm_recommend.MealTarget(slot="lunch", kcal=650, protein_g=42, carbs_g=70, fat_g=18),
        diet_tags=["pescatarian"],
        primary_diet="pescatarian",
        pref=None,
        provider="openai",
        used_protein_items=[],
        used_carb_items=[],
        prefer_fast_catalog=True,
    )

    assert recommendation.recipe.id == recipe.id
    assert recommendation.meta["fast_path"] is True


def test_lactose_intolerance_excludes_dairy_but_not_plant_milk():
    exclusions = ["lactose intolerant"]

    assert llm_recommend._text_violates_exclusions("30 g feta cheese", exclusions)
    assert llm_recommend._text_violates_exclusions("1 cup Greek yogurt", exclusions)
    assert llm_recommend._text_violates_exclusions("2 tbsp whey protein", exclusions)
    assert not llm_recommend._text_violates_exclusions("1 cup oat milk", exclusions)
    assert not llm_recommend._text_violates_exclusions("1 cup unsweetened soy milk", exclusions)


def test_catalog_recipe_with_feta_violates_lactose_exclusion():
    recipe = Recipe(
        title="Mediterranean Breakfast Bowl",
        meal_type="breakfast",
        ingredients=[
            {"name": "quinoa", "amount": "100", "unit": "g"},
            {"name": "feta cheese", "amount": "30", "unit": "g"},
        ],
    )

    assert llm_recommend._recipe_violates_exclusions(recipe, ["lactose intolerant"])


def test_structured_allergens_are_merged_with_custom_exclusions():
    pref = MagicMock()
    pref.ingredient_exclusions = "mushrooms"
    pref.allergies = ["milk", "tree_nuts", "sesame"]

    exclusions = llm_recommend._preference_exclusions(pref)

    assert exclusions == ["mushrooms", "milk", "tree_nuts", "sesame"]
    assert llm_recommend._text_violates_exclusions("30 g feta", exclusions)
    assert llm_recommend._text_violates_exclusions("2 tbsp tahini", exclusions)
    assert llm_recommend._text_violates_exclusions("1 oz almonds", exclusions)
    assert llm_recommend._text_violates_exclusions("1 cup mushrooms", exclusions)


def test_weekly_persistence_saves_ai_reason(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user = User(email="reason@example.com", password_hash="test")
        recipe = Recipe(title="Salmon Bowl", meal_type="lunch", ingredients=["salmon", "rice"])
        db.add_all([user, recipe])
        db.flush()
        recommendation = llm_recommend.SlotRecommendation(
            slot="lunch",
            target={"kcal": 600, "protein_g": 45, "carbs_g": 65, "fat_g": 18},
            recipe=llm_recommend.RecipePick(
                id=recipe.id,
                title=recipe.title,
                meal_type="lunch",
                ingredients=recipe.ingredients,
            ),
            reason="High protein and matched to the day's carbohydrate target.",
        )

        llm_recommend._persist_day_recommendations(db, user, "2026-08-07", [recommendation], None, "omnivore")
        db.commit()
        meal = db.query(PlanMeal).filter(PlanMeal.meal_type == "lunch").one()

        assert meal.meta["reason"] == recommendation.reason
        assert [item.name for item in meal.items] == ["salmon", "rice"]


def test_empty_slot_is_retried_until_it_has_an_applicable_meal(monkeypatch):
    attempts = iter(
        [
            ("empty", None, None, "duplicate", {"mode": "empty"}, None),
            ("empty", None, None, "duplicate", {"mode": "empty"}, None),
            (
                "create",
                None,
                {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
                "unique snack",
                {"mode": "create"},
                {
                    "title": "Greek Yogurt Apple Crunch",
                    "approx_macros": {"kcal": 300, "protein_g": 25, "carbs_g": 35, "fat_g": 8},
                    "protein_group": "dairy",
                    "protein_item": "greek yogurt",
                    "carb_item": "apple",
                },
            ),
        ]
    )
    monkeypatch.setattr(llm_recommend, "_top_k_candidates", lambda **_kwargs: [])
    monkeypatch.setattr(llm_recommend, "_llm_pick_or_create", lambda **_kwargs: next(attempts))

    recommendation = llm_recommend._recommend_for_single_meal(
        client=object(),
        db=MagicMock(),
        date="2026-07-29",
        tgt=llm_recommend.MealTarget(slot="snack", kcal=300, protein_g=25, carbs_g=35, fat_g=8),
        diet_tags=None,
        primary_diet="omnivore",
        pref=None,
        provider="openai",
        used_protein_items=[],
        used_carb_items=[],
        used_recipe_ids=set(),
        used_meal_keys=set(),
    )

    assert recommendation.ai_idea is not None
    assert recommendation.ai_idea["title"] == "Greek Yogurt Apple Crunch"
    assert recommendation.meta["slot_retry_attempts"] == 3


def test_exhausted_weekly_protein_groups_relax_cap_instead_of_returning_empty(monkeypatch):
    candidate = MagicMock()
    candidate.id = 42
    candidate.title = "Tuna and Rice Cakes"
    candidate.meal_type = "snack"
    candidate.protein_group = "fish"
    candidate.kcal = 360
    candidate.protein_g = 27
    candidate.carbs_g = 40
    candidate.fat_g = 10
    candidate.ingredients = ["tuna", "rice cakes"]
    candidate.instructions = "Assemble and serve."

    candidate_calls = []

    def fake_candidates(**kwargs):
        candidate_calls.append(kwargs.get("disallowed_protein_groups"))
        if kwargs.get("disallowed_protein_groups"):
            return []
        return [(candidate, {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}, 0.0)]

    def fake_pick(**kwargs):
        assert kwargs["banned_protein_groups"] is None
        return (
            "pick",
            candidate,
            {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
            "relaxed weekly variety cap",
            {"mode": "pick"},
            None,
        )

    monkeypatch.setattr(llm_recommend, "_top_k_candidates", fake_candidates)
    monkeypatch.setattr(llm_recommend, "_llm_pick_or_create", fake_pick)

    recommendation = llm_recommend._recommend_for_single_meal(
        client=object(),
        db=MagicMock(),
        date="2026-08-09",
        tgt=llm_recommend.MealTarget(slot="snack", kcal=360, protein_g=27, carbs_g=40, fat_g=10),
        diet_tags=["pescatarian"],
        primary_diet="pescatarian",
        pref=None,
        provider="openai",
        used_protein_items=[],
        used_carb_items=[],
        used_recipe_ids=set(),
        used_meal_keys=set(),
        week_protein_counts={
            ("snack", "dairy"): 2,
            ("snack", "fish"): 2,
            ("snack", "plant"): 2,
        },
    )

    assert candidate_calls == [{"dairy", "fish", "plant"}, None]
    assert recommendation.recipe is not None
    assert recommendation.meta["weekly_protein_cap_relaxed"] is True


def test_missing_slot_detection_covers_snacks_and_main_meals():
    items = [
        llm_recommend.SlotRecommendation(
            slot="breakfast",
            target={},
            ai_idea={"title": "Oats"},
        ),
        llm_recommend.SlotRecommendation(
            slot="snack",
            target={},
            meta={"mode": "empty"},
        ),
    ]

    assert llm_recommend._missing_recommendation_slots(items) == ["snack"]
