from datetime import date
from types import SimpleNamespace

from app.models import PlanMeal, Recipe
from app.routers import plans
from app.routers.llm_recommend import _apply_recipe_to_planmeal, _recipe_has_complete_cooking_guidance
from app.routers.plans import AIIdeaPayload, LLMNewRecipe, _apply_ai_idea_to_meal, _apply_recipe_to_meal
from app.services.training_nutrition import MacroTargets


def test_ai_cooking_steps_take_priority_over_description_and_persist_time():
    meal = PlanMeal(meal_type="dinner", items=[], meta={"reason": "Recovery meal"})
    idea = AIIdeaPayload(
        title="Lemon Herb Baked Cod",
        description="A high-protein recovery dinner.",
        ingredients=[{"name": "cod fillet", "amount": "6 oz"}],
        instructions=[
            "Heat the oven to 400°F and season the cod.",
            "Bake for 10–12 minutes, until opaque and 145°F in the center.",
        ],
        prep_time_min=10,
        cook_time_min=15,
        total_time_min=25,
    )

    _apply_ai_idea_to_meal(meal, idea, "dinner")

    assert meal.instructions == "\n".join(idea.instructions)
    assert "high-protein recovery" not in meal.instructions
    assert meal.meta == {
        "reason": "Recovery meal",
        "prep_time_min": 10,
        "cook_time_min": 15,
        "total_time_min": 25,
    }
    assert meal.items[0].qty == 6
    assert meal.items[0].unit == "oz"


def test_ai_ingredient_preserves_separate_amount_and_unit():
    meal = PlanMeal(meal_type="lunch", items=[], meta={})
    idea = AIIdeaPayload(
        title="Rice bowl",
        ingredients=[{"name": "brown rice", "amount": "1/2", "unit": "cup"}],
    )

    _apply_ai_idea_to_meal(meal, idea, "lunch")

    assert meal.items[0].qty == 0.5
    assert meal.items[0].unit == "cup"


def test_catalog_recipe_preserves_amount_field_when_applied():
    meal = PlanMeal(meal_type="dinner", items=[], meta={})
    recipe = Recipe(
        title="Measured dinner",
        meal_type="dinner",
        ingredients=[
            {"name": "chicken breast", "amount": "200", "unit": "g"},
            {"name": "olive oil", "amount": "1", "unit": "tbsp"},
        ],
    )

    _apply_recipe_to_meal(meal, recipe)

    assert [(item.qty, item.unit) for item in meal.items] == [(200, "g"), (1, "tbsp")]


def test_new_recipe_accepts_cooking_time():
    recipe = LLMNewRecipe(title="Fast dinner", total_time_min=20)

    assert recipe.total_time_min == 20


def test_catalog_recipe_replaces_stale_plan_timing():
    meal = PlanMeal(
        meal_type="dinner",
        items=[],
        meta={"reason": "Fueling", "prep_time_min": 8, "cook_time_min": 20, "total_time_min": 20},
    )
    recipe = Recipe(
        id=7,
        title="Safe chicken bowl",
        meal_type="dinner",
        kcal=500,
        protein_g=40,
        carbs_g=55,
        fat_g=12,
        ingredients=[
            {"name": "chicken breast", "amount": 120, "unit": "g"},
            {"name": "cooked rice", "amount": 180, "unit": "g"},
        ],
        instructions="Cook chicken to 165°F.\nServe with cooked rice.",
        prep_time_min=8,
        cook_time_min=15,
        total_time_min=23,
    )

    _apply_recipe_to_planmeal(meal, recipe)

    assert meal.meta == {"reason": "Fueling", "prep_time_min": 8, "cook_time_min": 15, "total_time_min": 23}


def test_catalog_recipe_strips_raw_footer_from_precooked_poultry():
    meal = PlanMeal(meal_type="dinner", items=[], meta={})
    recipe = Recipe(
        id=8,
        title="Turkey pasta",
        meal_type="dinner",
        ingredients=[
            {"name": "turkey breast, cooked", "amount": 96, "unit": "g"},
            {"name": "whole-wheat pasta, cooked", "amount": 156, "unit": "g"},
        ],
        instructions=(
            "Warm the cooked turkey and pasta until steaming.\n"
            "Cook until no longer pink and the internal temperature reaches 165°F."
        ),
        prep_time_min=5,
        cook_time_min=10,
        total_time_min=15,
    )

    _apply_recipe_to_planmeal(meal, recipe)

    assert meal.instructions == "Warm the cooked turkey and pasta until steaming."


def test_catalog_recipe_requires_its_own_complete_safe_timing():
    recipe = Recipe(
        title="Chicken bowl",
        meal_type="dinner",
        kcal=500,
        protein_g=40,
        carbs_g=55,
        fat_g=12,
        ingredients=[
            {"name": "chicken breast", "amount": 120, "unit": "g"},
            {"name": "cooked rice", "amount": 180, "unit": "g"},
        ],
        instructions="Cook chicken to 165°F.\nServe with cooked rice.",
        prep_time_min=8,
        cook_time_min=15,
        total_time_min=20,
    )

    assert not _recipe_has_complete_cooking_guidance(recipe)
    recipe.total_time_min = 23
    assert _recipe_has_complete_cooking_guidance(recipe)


def test_catalog_recipe_rejects_undersized_legacy_main_protein():
    recipe = Recipe(
        title="Herbed Salmon Couscous Supper",
        meal_type="dinner",
        kcal=650,
        protein_g=35,
        carbs_g=80,
        fat_g=20,
        ingredients=[
            {"name": "salmon fillet", "amount": 64, "amount_g": 64, "unit": "g"},
            {"name": "couscous cooked", "amount": 250, "amount_g": 250, "unit": "g"},
        ],
        instructions="Cook salmon to 145°F.\nServe with cooked couscous.",
        prep_time_min=5,
        cook_time_min=15,
        total_time_min=20,
    )

    assert not _recipe_has_complete_cooking_guidance(recipe)
    recipe.ingredients[0]["amount"] = 120
    recipe.ingredients[0]["amount_g"] = 120
    assert _recipe_has_complete_cooking_guidance(recipe)


def test_empty_plan_estimate_uses_same_profile_training_target_as_ai(monkeypatch):
    class QueryStub:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    expected = MacroTargets(kcal=2587.0, protein_g=163.0, carbs_g=320.0, fat_g=73.0)
    monkeypatch.setattr(
        plans,
        "calculate_training_nutrition",
        lambda **_kwargs: SimpleNamespace(final=expected),
    )
    db = SimpleNamespace(query=lambda *_args: QueryStub())

    seed = plans._seed_plan_heuristic(db, SimpleNamespace(id=1), date(2026, 10, 19))

    assert seed["totals"] == {
        "kcal": expected.kcal,
        "protein_g": expected.protein_g,
        "carbs_g": expected.carbs_g,
        "fat_g": expected.fat_g,
    }
