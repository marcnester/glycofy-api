from app.models import PlanMeal
from app.routers.plans import AIIdeaPayload, LLMNewRecipe, _apply_ai_idea_to_meal


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


def test_new_recipe_accepts_cooking_time():
    recipe = LLMNewRecipe(title="Fast dinner", total_time_min=20)

    assert recipe.total_time_min == 20
