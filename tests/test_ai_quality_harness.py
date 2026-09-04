from __future__ import annotations

import pytest

from app.routers import llm_recommend
from app.services.meal_quality import (
    EVALUATION_PROFILES,
    PROMPT_VERSION,
    QUALITY_POLICY_VERSION,
    evaluate_plan,
    validate_meal,
)


def meal(**overrides):
    value = {
        "title": "Tofu Rice Bowl",
        "ingredients": [
            {"name": "tofu", "amount": "6", "unit": "oz"},
            {"name": "rice", "amount": "1", "unit": "cup"},
            {"name": "broccoli", "amount": "1", "unit": "cup"},
        ],
        "instructions": ["Cook the rice.", "Sauté the tofu and broccoli, then serve."],
        "prep_time_min": 8,
        "cook_time_min": 15,
        "total_time_min": 23,
        "macros": {"kcal": 515, "protein_g": 35, "carbs_g": 65, "fat_g": 13},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("exclusion", "ingredient"),
    [
        ("milk", "whey protein"),
        ("lactose intolerant", "Greek yogurt"),
        ("tree_nuts", "almond butter"),
        ("sesame", "tahini"),
        ("soy", "tempeh"),
        ("wheat", "couscous"),
        ("shellfish", "shrimp"),
    ],
)
def test_allergen_aliases_are_hard_failures(exclusion, ingredient):
    candidate = meal(
        ingredients=[{"name": ingredient, "amount": "1", "unit": "cup"}, {"name": "rice", "amount": "1", "unit": "cup"}]
    )
    assert "excluded_ingredient" in validate_meal(candidate, exclusions=[exclusion]).codes()


@pytest.mark.parametrize(
    ("diet", "ingredient"),
    [("vegan", "Greek yogurt"), ("vegetarian", "chicken breast"), ("pescatarian", "beef steak")],
)
def test_diet_profiles_reject_incompatible_foods(diet, ingredient):
    candidate = meal(
        ingredients=[{"name": ingredient, "amount": "6", "unit": "oz"}, {"name": "rice", "amount": "1", "unit": "cup"}]
    )
    assert "diet_violation" in validate_meal(candidate, diet=diet).codes()


def test_nutrition_plausibility_checks_macro_energy_and_single_meal_bounds():
    mismatch = meal(macros={"kcal": 400, "protein_g": 100, "carbs_g": 100, "fat_g": 50})
    assert {"macro_energy_mismatch", "target_miss"} <= set(
        validate_meal(mismatch, target={"kcal": 500, "protein_g": 35, "carbs_g": 60, "fat_g": 15}).codes()
    )
    extreme = meal(macros={"kcal": 2200, "protein_g": 250, "carbs_g": 250, "fat_g": 100})
    assert "implausible_macros" in validate_meal(extreme).codes()


def test_recipe_timing_and_safe_doneness_are_consistent():
    salmon = meal(
        title="Baked Salmon and Rice",
        ingredients=[
            {"name": "raw salmon", "amount": "6", "unit": "oz"},
            {"name": "rice", "amount": "1", "unit": "cup"},
        ],
        instructions=["Bake the salmon.", "Serve with rice."],
        prep_time_min=10,
        cook_time_min=0,
        total_time_min=5,
    )
    codes = set(validate_meal(salmon).codes())
    assert {"inconsistent_timing", "uncooked_raw_protein", "missing_doneness_cue"} <= codes


def test_complete_meal_passes_every_evaluation_profile_and_versions_are_reported():
    for profile in EVALUATION_PROFILES:
        result = evaluate_plan([meal()], profile)
        assert result["pass_rate"] == 1.0
        assert result["prompt_version"] == PROMPT_VERSION
        assert result["quality_policy_version"] == QUALITY_POLICY_VERSION


def test_questionable_ai_meal_is_replaced_with_safe_fallback(monkeypatch):
    monkeypatch.setattr(
        llm_recommend,
        "_safe_openai_json_pick",
        lambda *_args, **_kwargs: (
            {
                "mode": "create",
                "new_recipe": {
                    "title": "Mystery Performance Bowl",
                    "ingredients": [
                        {"name": "rice", "amount": "1", "unit": "cup"},
                        {"name": "chicken", "amount": "6", "unit": "oz"},
                    ],
                    "instructions": ["Serve."],
                    "prep_time_min": 1,
                    "cook_time_min": 0,
                    "total_time_min": 1,
                    "protein_group": "poultry",
                    "protein_item": "chicken",
                    "carb_item": "rice",
                    "macro_estimate": {"kcal": 500, "protein_g": 120, "carbs_g": 5, "fat_g": 60},
                },
            },
            {"prompt_version": PROMPT_VERSION, "quality_policy_version": QUALITY_POLICY_VERSION},
        ),
    )

    mode, _recipe, _deltas, _reason, meta, idea = llm_recommend._llm_pick_or_create(
        client=object(),
        slot="lunch",
        tgt=llm_recommend.MealTarget(slot="lunch", kcal=600, protein_g=45, carbs_g=75, fat_g=15),
        candidates=[],
        date="2026-09-04",
        diet_tags=None,
        primary_diet="omnivore",
        user_pref=None,
        used_protein_items=[],
        used_carb_items=[],
        used_recipe_ids=set(),
        used_meal_keys=set(),
        allow_new_recipe=True,
    )

    assert mode == "create"
    assert idea is not None and idea["title"] != "Mystery Performance Bowl"
    assert meta["fallback"] == "quality_validation"
    assert "incomplete_instructions" in meta["quality"]["issues"]
