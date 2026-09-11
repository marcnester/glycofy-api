from __future__ import annotations

import pytest

from app.routers import llm_recommend
from app.services.meal_quality import (
    EVALUATION_PROFILES,
    PROMPT_VERSION,
    QUALITY_POLICY_VERSION,
    ensure_safe_doneness_instruction,
    evaluate_plan,
    ingredient_nutrition_totals,
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


def test_provisional_target_miss_can_be_recorded_as_a_warning():
    candidate = meal(macros={"kcal": 400, "protein_g": 30, "carbs_g": 45, "fat_g": 11})
    report = validate_meal(
        candidate,
        target={"kcal": 600, "protein_g": 30, "carbs_g": 45, "fat_g": 11},
        target_miss_severity="warning",
    )
    assert "target_miss" in report.codes()
    assert report.safe


def test_missing_doneness_cue_is_added_deterministically():
    candidate = meal(
        ingredients=[
            {"name": "chicken breast", "amount": "150", "unit": "g"},
            {"name": "rice", "amount": "200", "unit": "g"},
        ],
        instructions=["Heat a skillet.", "Cook the chicken and serve with rice."],
    )
    repaired = ensure_safe_doneness_instruction(candidate)
    assert "165°F" in repaired["instructions"][-1]
    assert "missing_doneness_cue" not in validate_meal(repaired).codes()


def test_itemized_nutrition_is_required_and_must_equal_meal_totals():
    candidate = meal()
    assert "missing_ingredient_nutrition" in validate_meal(candidate, require_ingredient_nutrition=True).codes()

    candidate["ingredients"] = [
        {
            "name": "tofu",
            "amount": "6",
            "unit": "oz",
            "nutrition": {"kcal": 250, "protein_g": 30, "carbs_g": 10, "fat_g": 10},
        },
        {
            "name": "rice",
            "amount": "1",
            "unit": "cup",
            "nutrition": {"kcal": 250, "protein_g": 5, "carbs_g": 55, "fat_g": 3},
        },
    ]
    assert ingredient_nutrition_totals(candidate) == {
        "kcal": 500.0,
        "protein_g": 35.0,
        "carbs_g": 65.0,
        "fat_g": 13.0,
    }
    assert "ingredient_macro_mismatch" not in validate_meal(candidate, require_ingredient_nutrition=True).codes()
    candidate["macros"]["protein_g"] = 55
    assert "ingredient_macro_mismatch" in validate_meal(candidate, require_ingredient_nutrition=True).codes()


@pytest.mark.parametrize(
    ("ingredients", "macros", "expected_issue"),
    [
        (
            [
                {
                    "name": "grilled chicken",
                    "amount": "6",
                    "unit": "oz",
                    "nutrition": {"kcal": 300, "protein_g": 45, "carbs_g": 0, "fat_g": 13},
                },
                {
                    "name": "romaine lettuce",
                    "amount": "2",
                    "unit": "cups",
                    "nutrition": {"kcal": 180, "protein_g": 0, "carbs_g": 45, "fat_g": 0},
                },
            ],
            {"kcal": 480, "protein_g": 45, "carbs_g": 45, "fat_g": 13},
            "missing_carb_source",
        ),
        (
            [
                {
                    "name": "mixed vegetables",
                    "amount": "2",
                    "unit": "cups",
                    "nutrition": {"kcal": 220, "protein_g": 30, "carbs_g": 25, "fat_g": 0},
                },
                {
                    "name": "coconut milk",
                    "amount": "1/2",
                    "unit": "cup",
                    "nutrition": {"kcal": 180, "protein_g": 0, "carbs_g": 5, "fat_g": 20},
                },
            ],
            {"kcal": 400, "protein_g": 30, "carbs_g": 30, "fat_g": 20},
            "missing_protein_source",
        ),
        (
            [
                {
                    "name": "shrimp",
                    "amount": "6",
                    "unit": "oz",
                    "nutrition": {"kcal": 250, "protein_g": 40, "carbs_g": 0, "fat_g": 20},
                },
                {
                    "name": "corn tortillas",
                    "amount": "2",
                    "unit": "items",
                    "nutrition": {"kcal": 200, "protein_g": 5, "carbs_g": 45, "fat_g": 0},
                },
            ],
            {"kcal": 450, "protein_g": 45, "carbs_g": 45, "fat_g": 20},
            "missing_fat_source",
        ),
    ],
)
def test_claimed_macros_require_real_food_sources(ingredients, macros, expected_issue):
    candidate = meal(ingredients=ingredients, macros=macros)
    assert expected_issue in validate_meal(candidate, require_ingredient_nutrition=True).codes()


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


def test_questionable_ai_meal_fails_closed_without_verified_fallback(monkeypatch):
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

    assert mode == "empty"
    assert idea is None
    assert meta["fallback"] == "quality_validation"
    assert "incomplete_instructions" in meta["quality"]["issues"]
