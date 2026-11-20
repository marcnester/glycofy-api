from types import SimpleNamespace

import pytest

from app.routers import llm_recommend
from app.services.meal_quality import practical_maximum_grams, validate_meal
from app.services.usda_nutrition import FDCMatch, USDANutritionError, _food_identity_matches, verify_ingredients


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
