from __future__ import annotations

import pytest

from app.routers import llm_recommend
from app.services.meal_quality import (
    ALLERGEN_ALIASES,
    EVALUATION_PROFILES,
    PROMPT_VERSION,
    QUALITY_POLICY_VERSION,
    SUPPORTED_ALLERGENS,
    SUPPORTED_DIETS,
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


def universally_safe_meal():
    return meal(
        title="Lentil Rice Bowl",
        ingredients=[
            {"name": "lentils", "amount": "1", "unit": "cup"},
            {"name": "brown rice", "amount": "1", "unit": "cup"},
            {"name": "broccoli", "amount": "1", "unit": "cup"},
            {"name": "olive oil", "amount": "1", "unit": "tbsp"},
        ],
        instructions=["Cook the lentils and rice until tender.", "Add broccoli and olive oil, then serve."],
        macros={"kcal": 600, "protein_g": 30, "carbs_g": 84, "fat_g": 16},
    )


@pytest.mark.parametrize("diet", SUPPORTED_DIETS)
@pytest.mark.parametrize("allergen", SUPPORTED_ALLERGENS)
def test_every_supported_diet_allergen_pair_accepts_a_safe_complete_meal(diet, allergen):
    report = validate_meal(universally_safe_meal(), diet=diet, exclusions=[allergen])
    assert report.safe, (diet, allergen, report.codes())


@pytest.mark.parametrize(
    ("allergen", "alias"),
    [(allergen, alias) for allergen in SUPPORTED_ALLERGENS for alias in sorted(ALLERGEN_ALIASES[allergen])],
)
def test_every_known_alias_for_every_supported_allergen_is_rejected(allergen, alias):
    candidate = meal(
        title="Unsafe test meal",
        ingredients=[
            {"name": alias, "amount": "1", "unit": "serving"},
            {"name": "rice", "amount": "1", "unit": "cup"},
        ],
    )
    assert "excluded_ingredient" in validate_meal(candidate, exclusions=[allergen]).codes()


@pytest.mark.parametrize(
    "alternative",
    ["oat milk", "rice milk", "coconut milk", "dairy-free yogurt", "vegan cheese"],
)
def test_explicit_plant_dairy_alternatives_are_not_false_positive_milk_hits(alternative):
    candidate = meal(
        ingredients=[
            {"name": alternative, "amount": "1", "unit": "cup"},
            {"name": "rice", "amount": "1", "unit": "cup"},
        ]
    )
    assert "excluded_ingredient" not in validate_meal(candidate, exclusions=["milk"]).codes()
    assert "diet_violation" not in validate_meal(candidate, diet="vegan").codes()


@pytest.mark.parametrize(
    ("diet", "ingredient"),
    [
        ("pescatarian", "duck breast"),
        ("pescatarian", "venison steak"),
        ("vegetarian", "sardines"),
        ("vegetarian", "scallops"),
        ("vegan", "ricotta"),
        ("vegan", "gelatin"),
        ("vegan", "mayonnaise"),
    ],
)
def test_extended_diet_aliases_are_rejected(diet, ingredient):
    candidate = meal(
        ingredients=[
            {"name": ingredient, "amount": "1", "unit": "serving"},
            {"name": "rice", "amount": "1", "unit": "cup"},
        ]
    )
    assert "diet_violation" in validate_meal(candidate, diet=diet).codes()


def test_certification_profiles_cover_every_diet_allergen_pair_and_high_risk_combinations():
    covered = {
        (profile["diet"], profile["exclusions"][0])
        for profile in EVALUATION_PROFILES
        if len(profile["exclusions"]) == 1
    }
    assert covered == {(diet, allergen) for diet in SUPPORTED_DIETS for allergen in SUPPORTED_ALLERGENS}
    assert any(len(profile["exclusions"]) == len(SUPPORTED_ALLERGENS) for profile in EVALUATION_PROFILES)


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


def test_ground_poultry_uses_poultry_temperature_not_ground_red_meat_temperature():
    candidate = meal(
        ingredients=[
            {"name": "ground turkey", "amount": "150", "unit": "g"},
            {"name": "rice", "amount": "200", "unit": "g"},
        ],
        instructions=["Brown the ground turkey and serve with rice."],
    )
    repaired = ensure_safe_doneness_instruction(candidate)
    assert "165°F" in repaired["instructions"][-1]


def test_ready_to_eat_poultry_is_not_given_raw_meat_doneness_directions():
    candidate = meal(
        title="Turkey Hummus Pita Snack",
        ingredients=[
            {"name": "roasted turkey breast", "amount": 75, "amount_g": 75, "unit": "g"},
            {"name": "whole wheat pita", "amount": 60, "amount_g": 60, "unit": "g"},
        ],
        instructions=["Warm the pita for 2 minutes.", "Fill with roasted turkey and serve."],
        cook_time_min=2,
    )

    repaired = ensure_safe_doneness_instruction(candidate)

    assert repaired["instructions"] == candidate["instructions"]
    assert "missing_doneness_cue" not in validate_meal(repaired).codes()


def test_ready_to_eat_poultry_loses_contradictory_raw_footer():
    candidate = meal(
        title="Turkey Pasta",
        ingredients=[
            {"name": "turkey breast, cooked", "amount": 96, "amount_g": 96, "unit": "g"},
            {"name": "whole-wheat pasta, cooked", "amount": 156, "amount_g": 156, "unit": "g"},
        ],
        instructions=[
            "Warm the cooked turkey and pasta until steaming.",
            "Cook until no longer pink and the internal temperature reaches 165°F.",
        ],
    )

    repaired = ensure_safe_doneness_instruction(candidate)

    assert repaired["instructions"] == ["Warm the cooked turkey and pasta until steaming."]


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


def test_total_time_includes_prep_and_cooking():
    candidate = meal(
        title="Beef Pasta",
        ingredients=[
            {"name": "lean ground beef", "amount": 120, "amount_g": 120, "unit": "g"},
            {"name": "whole-wheat pasta", "amount": 85, "amount_g": 85, "unit": "g"},
        ],
        instructions=[
            "Boil the pasta until tender, then drain.",
            "Cook the beef until it reaches 160°F/71°C, then combine with the pasta.",
        ],
        prep_time_min=8,
        cook_time_min=20,
        total_time_min=20,
    )

    assert "inconsistent_timing" in validate_meal(candidate).codes()


def test_ground_lamb_requires_160f_and_rejects_conflicting_lower_temperature():
    candidate = meal(
        title="Lamb Keema",
        ingredients=[
            {"name": "ground lamb", "amount": 150, "amount_g": 150, "unit": "g"},
            {"name": "peas", "amount": 120, "amount_g": 120, "unit": "g"},
        ],
        instructions=[
            "Cook ground lamb until its internal temperature reaches 160°F.",
            "Serve when its internal temperature reaches 145°F.",
        ],
    )

    assert "unsafe_internal_temperature" in validate_meal(candidate).codes()


def test_missing_seasoning_and_impractical_food_portions_are_rejected():
    candidate = meal(
        slot="dinner",
        protein_item="salmon",
        ingredients=[
            {"name": "salmon", "amount": 45, "amount_g": 45, "unit": "g"},
            {"name": "whole wheat pasta", "amount": 160, "amount_g": 160, "unit": "g"},
            {"name": "walnuts", "amount": 2, "amount_g": 2, "unit": "g"},
        ],
        instructions=["Season the salmon with paprika.", "Cook and serve with pasta."],
    )

    codes = set(validate_meal(candidate).codes())
    assert {"unlisted_instruction_ingredient", "impractical_serving", "impractical_primary_protein"} <= codes


def test_token_fruit_portions_are_rejected():
    candidate = meal(
        ingredients=[
            {"name": "low-fat cottage cheese", "amount": 220, "amount_g": 220, "unit": "g"},
            {"name": "banana", "amount": 7, "amount_g": 7, "unit": "g"},
        ]
    )

    assert "impractical_serving" in validate_meal(candidate).codes()


@pytest.mark.parametrize(
    ("name", "amount"),
    [
        ("low-fat Greek yogurt", 7),
        ("carrot", 5),
        ("cucumber", 8),
    ],
)
def test_token_dairy_and_vegetable_portions_are_rejected(name, amount):
    candidate = meal(
        ingredients=[
            {"name": name, "amount": amount, "amount_g": amount, "unit": "g"},
            {"name": "rolled oats", "amount": 60, "amount_g": 60, "unit": "g"},
        ]
    )

    assert "impractical_serving" in validate_meal(candidate).codes()


def test_generic_protein_item_still_enforces_main_animal_protein_floor():
    candidate = meal(
        title="Herbed Salmon Couscous",
        slot="dinner",
        protein_item="fish",
        protein_group="fish",
        ingredients=[
            {"name": "salmon fillet", "amount": 64, "amount_g": 64, "unit": "g"},
            {"name": "couscous", "amount": 120, "amount_g": 120, "unit": "g"},
        ],
    )

    assert "impractical_primary_protein" in validate_meal(candidate).codes()


def test_overnight_or_long_chill_time_must_be_in_advertised_total():
    overnight = meal(
        title="Overnight Oats",
        instructions=["Combine the ingredients.", "Refrigerate overnight."],
        prep_time_min=8,
        cook_time_min=0,
        total_time_min=8,
    )
    chilled = meal(
        instructions=["Combine the ingredients.", "Chill for 30 minutes."],
        prep_time_min=8,
        cook_time_min=0,
        total_time_min=8,
    )

    assert "inconsistent_wait_time" in validate_meal(overnight).codes()
    assert "inconsistent_wait_time" in validate_meal(chilled).codes()


def test_soaking_step_requires_an_explicit_soak_duration():
    candidate = meal(
        title="Black Bean Bowl",
        ingredients=[
            {"name": "black beans, dry", "amount": 45, "amount_g": 45, "unit": "g"},
            {"name": "brown rice, cooked", "amount": 150, "amount_g": 150, "unit": "g"},
        ],
        instructions=[
            "Soak the black beans, drain, and simmer until tender for 25 minutes.",
            "Serve with rice.",
        ],
        prep_time_min=10,
        cook_time_min=25,
        total_time_min=35,
    )

    assert "unspecified_soak_time" in validate_meal(candidate).codes()


def test_total_time_includes_rest_after_cooking():
    candidate = meal(
        instructions=["Simmer rice for 35 minutes.", "Rest for 5 minutes, then serve."],
        prep_time_min=10,
        cook_time_min=35,
        total_time_min=35,
    )

    assert "inconsistent_wait_time" in validate_meal(candidate).codes()


def test_dry_oats_cannot_be_served_immediately_without_cooking_or_soaking():
    candidate = meal(
        title="Yogurt Oat Bowl",
        ingredients=[
            {"name": "rolled oats", "amount": 50, "amount_g": 50, "unit": "g"},
            {"name": "Greek yogurt", "amount": 180, "amount_g": 180, "unit": "g"},
        ],
        instructions=["Combine oats and yogurt.", "Serve immediately."],
        prep_time_min=5,
        cook_time_min=0,
        total_time_min=5,
    )

    assert "uncooked_dry_grain" in validate_meal(candidate).codes()


def test_complete_meal_passes_every_evaluation_profile_and_versions_are_reported():
    for profile in EVALUATION_PROFILES:
        result = evaluate_plan([universally_safe_meal()], profile)
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
