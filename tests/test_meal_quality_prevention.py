from types import SimpleNamespace

import pytest

from app.routers import llm_recommend
from app.services.meal_quality import practical_maximum_grams, validate_meal
from app.services.usda_nutrition import FDCMatch, USDANutritionError, _food_identity_matches, verify_ingredients


@pytest.mark.parametrize("name", ["Cheese, cottage, low fat", "Yogurt, Greek, plain, nonfat"])
def test_usda_dairy_word_order_retains_protein_source(name):
    meal = {
        "title": "Dairy fruit snack",
        "slot": "snack",
        "prep_time_min": 5,
        "cook_time_min": 0,
        "total_time_min": 5,
        "ingredients": [
            {
                "name": name,
                "amount": 250,
                "unit": "g",
                "nutrition": {
                    "kcal": 175,
                    "protein_g": 25,
                    "carbs_g": 10,
                    "fat_g": 3.9,
                },
            },
            {
                "name": "peach raw",
                "amount": 100,
                "unit": "g",
                "nutrition": {
                    "kcal": 40,
                    "protein_g": 1,
                    "carbs_g": 9,
                    "fat_g": 0.2,
                },
            },
        ],
        "instructions": ["Spoon the dairy into a bowl.", "Top with sliced peach and serve."],
        "macros": {"kcal": 215, "protein_g": 26, "carbs_g": 19, "fat_g": 4.1},
    }
    assert validate_meal(meal, require_ingredient_nutrition=True).safe
    meal["ingredients"][0]["name"] = "mixed vegetables"
    assert "missing_protein_source" in validate_meal(meal, require_ingredient_nutrition=True).codes()


@pytest.mark.parametrize(
    ("name", "grams"), [("apple", 450), ("strawberries", 450), ("brown rice dry", 256), ("whole wheat flour", 155)]
)
def test_rejects_audited_oversized_portions(name, grams):
    report = validate_meal(
        {
            "title": "Meal",
            "ingredients": [{"name": name, "amount": grams, "unit": "g"}],
            "instructions": ["Prepare food.", "Serve."],
        }
    )
    assert "oversized_portion" in report.codes()
    assert practical_maximum_grams(name) < grams


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("apple raw", "Apricots, raw"),
        ("whole wheat flour", "Almonds, raw"),
        ("rice cakes", "Cereal crisp rice"),
        ("skyr", "Buttermilk"),
        ("red lentil pasta dry", "Lentils, dry"),
        ("whole wheat pasta dry", "Pancake mix, whole wheat, dry"),
        ("brown rice dry", "Beans and brown rice, prepared"),
        ("chicken thigh raw", "Chicken thigh cooked"),
    ],
)
def test_rejects_wrong_nutrient_identity(name, source):
    assert not _food_identity_matches(name, source)


def test_verification_checks_display_name_against_resolved_source():
    match = FDCMatch(1, "Apricots, raw", "Foundation", {"kcal": 48, "protein_g": 1.4, "carbs_g": 11, "fat_g": 0.4})
    with pytest.raises(USDANutritionError, match="does not match"):
        verify_ingredients(
            [{"name": "apple", "usda_search_query": "apricots raw", "amount": 100, "amount_g": 100, "unit": "g"}],
            resolved_foods={"apricots raw": match},
        )


def test_catches_unlisted_food_and_duplicate_grain_cooking():
    report = validate_meal(
        {
            "title": "Rice bowl",
            "ingredients": [{"name": "rice dry", "amount": 100, "unit": "g"}],
            "instructions": ["Cook the rice.", "Boil the rice and add chicken."],
        }
    )
    assert {"unlisted_instruction_ingredient", "duplicate_cooking_step"} <= set(report.codes())


def test_renamed_same_ingredients_cannot_fill_week():
    days = [{"date": f"2026-11-{day}"} for day in (19, 20, 21)]
    items = {
        day["date"]: {
            "snack": llm_recommend.SlotRecommendation(
                slot="snack",
                target={},
                ai_idea={
                    "title": f"Snack {i}",
                    "ingredients": [{"name": "yogurt", "amount_g": 200}, {"name": "banana", "amount_g": 100}],
                },
            )
        }
        for i, day in enumerate(days)
    }
    _, removed = llm_recommend._deduplicate_week_batch(days, items)
    assert removed == 1


def test_locked_plan_is_returned_before_meals_or_recipes_are_changed(monkeypatch):
    monkeypatch.setattr(llm_recommend, "_get_or_create_plan", lambda *_: SimpleNamespace(locked=True))
    monkeypatch.setattr(
        llm_recommend, "_ensure_plan_meals", lambda *_: pytest.fail("locked meals were accessed for mutation")
    )
    result = llm_recommend._persist_day_recommendations(None, SimpleNamespace(id=1), "2026-11-19", [], None, "omnivore")
    assert result["reason"] == "locked"
    assert result["applied"] == result["created_recipes"] == 0


def test_reused_recipe_cannot_bypass_nutrient_identity_check():
    report = validate_meal(
        {
            "title": "Apple yogurt",
            "ingredients": [
                {"name": "apple", "amount": 150, "unit": "g", "nutrition_source": {"description": "Apricots, raw"}},
                {"name": "yogurt", "amount": 200, "unit": "g"},
            ],
            "instructions": ["Slice the apple.", "Serve with yogurt."],
        }
    )
    assert "nutrient_source_mismatch" in report.codes()


def test_directions_cannot_use_more_than_saved_ingredient_quantity():
    report = validate_meal(
        {
            "title": "Rice bowl",
            "ingredients": [{"name": "rice", "amount": 100, "unit": "g"}, {"name": "tofu", "amount": 150, "unit": "g"}],
            "instructions": ["Cook 200 g rice until tender.", "Serve with tofu."],
        }
    )
    assert "instruction_quantity_mismatch" in report.codes()


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("apple raw", "Apples, raw"),
        ("rice cakes", "Snacks, rice cakes, brown rice"),
        ("chickpeas cooked", "Chickpeas (garbanzo beans), cooked, boiled"),
        ("whole wheat flour", "Wheat flour, whole-grain"),
    ],
)
def test_valid_food_matches_remain_eligible(name, source):
    assert _food_identity_matches(name, source)


@pytest.mark.parametrize("pending_nutrition", [None, {"kcal": 130, "protein_g": 2.7, "carbs_g": 28, "fat_g": 0.3}])
def test_save_rejects_unverified_candidate_before_touching_any_meal(monkeypatch, pending_nutrition):
    from fastapi import HTTPException

    monkeypatch.setattr(llm_recommend, "_get_or_create_plan", lambda *_: SimpleNamespace(locked=False))
    monkeypatch.setattr(
        llm_recommend, "_ensure_plan_meals", lambda *_: pytest.fail("existing meals must stay untouched")
    )
    measured = {"name": "rice cooked", "amount_g": 100, "amount": 100, "unit": "g"}
    verified = llm_recommend.SlotRecommendation(
        slot="breakfast",
        target={},
        ai_idea={
            "ingredients": [{**measured, "nutrition": {"kcal": 130, "protein_g": 2.7, "carbs_g": 28, "fat_g": 0.3}}],
        },
    )
    unresolved = llm_recommend.SlotRecommendation(
        slot="lunch",
        target={},
        ai_idea={
            "ingredients": [{**measured, "nutrition": pending_nutrition, "nutrition_status": "pending_validation"}],
        },
    )
    with pytest.raises(HTTPException) as error:
        llm_recommend._persist_day_recommendations(
            None, SimpleNamespace(id=1), "2026-11-21", [verified, unresolved], None, "omnivore"
        )
    assert error.value.status_code == 422
    assert error.value.detail["reason"] == "nutrition_unverified"


def test_save_requires_usda_sources_when_authoritative_matching_is_enabled(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(llm_recommend.settings, "USDA_FDC_API_KEY", "test-key")
    monkeypatch.setattr(llm_recommend, "_get_or_create_plan", lambda *_: SimpleNamespace(locked=False))
    monkeypatch.setattr(
        llm_recommend, "_ensure_plan_meals", lambda *_: pytest.fail("must reject before modifying meals")
    )
    ingredients = [
        {
            "name": "rice cooked",
            "amount_g": 100,
            "amount": 100,
            "unit": "g",
            "nutrition": {"kcal": 130, "protein_g": 2.7, "carbs_g": 28, "fat_g": 0.3},
        }
    ]
    item = llm_recommend.SlotRecommendation(slot="lunch", target={}, ai_idea={"ingredients": ingredients})
    assert not llm_recommend._recipe_has_reconciled_nutrition(SimpleNamespace(ingredients=ingredients))
    with pytest.raises(HTTPException) as error:
        llm_recommend._persist_day_recommendations(None, SimpleNamespace(id=1), "2026-11-21", [item], None, "omnivore")
    assert error.value.detail["reason"] == "nutrition_unverified"
    ingredients[0]["nutrition_source"] = {"provider": "USDA FoodData Central", "fdc_id": 169704}
    assert llm_recommend._ingredients_have_usda_sources(ingredients)
