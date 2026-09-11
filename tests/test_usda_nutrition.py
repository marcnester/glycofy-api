from __future__ import annotations

import pytest

from app.services import usda_nutrition
from app.services.usda_nutrition import (
    FDCMatch,
    USDANutritionError,
    USDAUnavailableError,
    fit_portions_to_targets,
    resolve_foods,
    select_match,
    verify_ingredients,
    verify_ingredients_resilient,
)


def _food(fdc_id: int, description: str, *, data_type: str = "Foundation") -> dict:
    values = {1008: 165, 1003: 31, 1005: 0, 1004: 3.6}
    return {
        "fdcId": fdc_id,
        "description": description,
        "dataType": data_type,
        "foodNutrients": [{"nutrientId": nutrient_id, "value": value} for nutrient_id, value in values.items()],
    }


def test_select_match_rejects_unrequested_processing():
    foods = [
        _food(1, "Chicken breast, breaded, cooked"),
        _food(2, "Chicken breast, meat only, cooked, roasted"),
    ]

    match = select_match("chicken breast cooked", foods)

    assert match.fdc_id == 2
    assert match.data_type == "Foundation"


def test_select_match_fails_closed_when_ambiguous():
    green = _food(11, "Banana raw green")
    green["foodNutrients"] = [
        {"nutrientId": 1008, "value": 120},
        {"nutrientId": 1003, "value": 1.1},
        {"nutrientId": 1005, "value": 31},
        {"nutrientId": 1004, "value": 0.3},
    ]
    with pytest.raises(USDANutritionError, match="Ambiguous"):
        select_match(
            "banana raw",
            [
                _food(10, "Banana raw yellow"),
                green,
            ],
        )


def test_select_match_accepts_nutritionally_equivalent_usda_tie():
    match = select_match(
        "banana raw",
        [
            _food(10, "Banana raw yellow"),
            _food(11, "Banana raw green"),
        ],
    )

    assert match.fdc_id == 10


def test_select_match_tolerates_natural_language_modifiers():
    match = select_match(
        "extra virgin olive oil",
        [_food(12, "Oil, olive, salad or cooking")],
    )

    assert match.fdc_id == 12


def test_select_match_normalizes_plural_food_words():
    match = select_match(
        "whole wheat tortillas",
        [_food(13, "Tortilla, whole-wheat, ready-to-eat", data_type="Survey (FNDDS)")],
    )

    assert match.fdc_id == 13


@pytest.mark.parametrize(
    ("query", "description"),
    [
        ("salmon baked", "Fish, salmon, cooked, dry heat"),
        ("chicken breast grilled", "Chicken breast, cooked, roasted"),
        ("broccoli steamed", "Broccoli, cooked"),
    ],
)
def test_select_match_accepts_equivalent_usda_preparation_terms(query, description):
    match = select_match(query, [_food(14, description)])

    assert match.fdc_id == 14


def test_select_match_keeps_raw_and_cooked_foods_distinct():
    with pytest.raises(USDANutritionError, match="No unambiguous"):
        select_match("chicken breast raw", [_food(15, "Chicken breast, cooked, roasted")])


def test_select_match_accepts_omitted_zero_macros_when_energy_is_explained():
    oil = _food(16, "Oil, olive")
    oil["foodNutrients"] = [
        {"nutrientId": 1008, "value": 884},
        {"nutrientId": 1004, "value": 100},
    ]

    match = select_match("olive oil", [oil])

    assert match.nutrients_per_100g == {"kcal": 884.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 100.0}


def test_select_match_accepts_foundation_atwater_energy():
    oats = _food(18, "Oats, whole grain, rolled, old fashioned")
    oats["foodNutrients"] = [
        {"nutrientId": 2047, "value": 379},
        {"nutrientId": 1003, "value": 13.2},
        {"nutrientId": 1005, "value": 67.7},
        {"nutrientId": 1004, "value": 6.5},
    ]

    match = select_match("rolled oats", [oats])

    assert match.nutrients_per_100g["kcal"] == 379.0


def test_select_match_prefers_food_specific_atwater_energy():
    oats = _food(19, "Oats, whole grain, rolled, old fashioned")
    oats["foodNutrients"] = [
        {"nutrientId": 1008, "value": 370},
        {"nutrientId": 2047, "value": 375},
        {"nutrientId": 2048, "value": 379},
        {"nutrientId": 1003, "value": 13.2},
        {"nutrientId": 1005, "value": 67.7},
        {"nutrientId": 1004, "value": 6.5},
    ]

    match = select_match("rolled oats", [oats])

    assert match.nutrients_per_100g["kcal"] == 379.0


def test_select_match_rejects_missing_macro_when_energy_is_not_explained():
    incomplete = _food(17, "Mystery food")
    incomplete["foodNutrients"] = [
        {"nutrientId": 1008, "value": 300},
        {"nutrientId": 1003, "value": 10},
    ]

    with pytest.raises(USDANutritionError, match="No unambiguous"):
        select_match("mystery food", [incomplete])


def test_verify_ingredients_replaces_model_values_with_usda(monkeypatch):
    match = FDCMatch(
        fdc_id=171077,
        description="Chicken breast, meat only, cooked, roasted",
        data_type="SR Legacy",
        nutrients_per_100g={"kcal": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    )
    monkeypatch.setattr(usda_nutrition, "lookup_food", lambda query: match)

    ingredients = verify_ingredients(
        [
            {
                "name": "roasted chicken breast",
                "amount": "150",
                "unit": "g",
                "amount_g": 150,
                "usda_search_query": "chicken breast cooked roasted",
                "nutrition": {"kcal": 999, "protein_g": 999, "carbs_g": 999, "fat_g": 999},
            }
        ]
    )

    assert ingredients[0]["nutrition"] == {"kcal": 247.5, "protein_g": 46.5, "carbs_g": 0.0, "fat_g": 5.4}
    assert ingredients[0]["food_ref_id"] == "171077"
    assert ingredients[0]["nutrition_source"]["provider"] == "USDA FoodData Central"


def test_resilient_verification_keeps_valid_measured_food_provisional(monkeypatch):
    queued = []
    monkeypatch.setattr(
        usda_nutrition,
        "lookup_food",
        lambda _query: (_ for _ in ()).throw(USDAUnavailableError("temporarily unavailable")),
    )
    monkeypatch.setattr(usda_nutrition, "enqueue_nutrition_validation", lambda query, _exc: queued.append(query))

    ingredients, unresolved = verify_ingredients_resilient(
        [{"name": "banana", "amount": 120, "amount_g": 120, "unit": "g"}]
    )

    assert unresolved == 1
    assert ingredients[0]["nutrition_status"] == "pending_validation"
    assert ingredients[0]["nutrition"] is None
    assert queued == ["banana"]


def test_resilient_verification_still_rejects_invalid_measurement(monkeypatch):
    monkeypatch.setattr(usda_nutrition, "enqueue_nutrition_validation", lambda *_args: pytest.fail("must not queue"))

    with pytest.raises(USDANutritionError, match="cannot be verified"):
        verify_ingredients_resilient([{"name": "banana", "amount": 1, "amount_g": 120, "unit": "item"}])


def test_fit_portions_uses_verified_foods_to_reach_macro_targets():
    ingredients = [
        {
            "name": "chicken breast",
            "amount": 100.0,
            "unit": "g",
            "amount_g": 100.0,
            "nutrition": {"kcal": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
        },
        {
            "name": "cooked rice",
            "amount": 100.0,
            "unit": "g",
            "amount_g": 100.0,
            "nutrition": {"kcal": 130.0, "protein_g": 2.7, "carbs_g": 28.0, "fat_g": 0.3},
        },
        {
            "name": "olive oil",
            "amount": 10.0,
            "unit": "g",
            "amount_g": 10.0,
            "nutrition": {"kcal": 88.4, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 10.0},
        },
        {
            "name": "salt",
            "amount": 1.0,
            "unit": "g",
            "amount_g": 1.0,
            "nutrition": {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0},
        },
    ]
    targets = {"kcal": 650.0, "protein_g": 50.0, "carbs_g": 75.0, "fat_g": 17.0}

    fitted = fit_portions_to_targets(ingredients, targets)
    totals = {
        name: sum(float(item["nutrition"][name]) for item in fitted)
        for name in ("kcal", "protein_g", "carbs_g", "fat_g")
    }

    assert all(abs(totals[name] - targets[name]) / targets[name] <= 0.18 for name in totals)
    assert fitted[3] == ingredients[3]
    assert fitted[0]["nutrition"]["protein_g"] != ingredients[0]["nutrition"]["protein_g"]


def test_verify_ingredients_falls_back_to_concise_name():
    match = FDCMatch(
        fdc_id=171077,
        description="Chicken breast, cooked",
        data_type="Foundation",
        nutrients_per_100g={"kcal": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
    )
    resolved = {
        "premium boneless chicken breast cooked": USDANutritionError("no match"),
        "chicken breast": match,
    }

    ingredients = verify_ingredients(
        [
            {
                "name": "chicken breast",
                "amount": 100,
                "unit": "g",
                "amount_g": 100,
                "usda_search_query": "premium boneless chicken breast cooked",
            }
        ],
        resolved_foods=resolved,
    )

    assert ingredients[0]["food_ref_id"] == "171077"


def test_lookup_reports_rejected_key_without_logging_url_or_query(monkeypatch, caplog):
    usda_nutrition.lookup_food.cache_clear()

    class RejectedClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            request = usda_nutrition.httpx.Request("POST", usda_nutrition.FDC_API_URL)
            response = usda_nutrition.httpx.Response(403, request=request)
            raise usda_nutrition.httpx.HTTPStatusError("forbidden", request=request, response=response)

    monkeypatch.setattr(usda_nutrition.settings, "USDA_FDC_API_KEY", "super-secret")
    monkeypatch.setattr(usda_nutrition.httpx, "Client", RejectedClient)

    with pytest.raises(USDANutritionError, match="rejected"):
        usda_nutrition.lookup_food("private food query")

    assert "super-secret" not in caplog.text
    assert "private food query" not in caplog.text


def test_lookup_retries_transient_usda_failure(monkeypatch):
    usda_nutrition.lookup_food.cache_clear()
    attempts = []

    class TransientClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            attempts.append(1)
            request = usda_nutrition.httpx.Request("POST", usda_nutrition.FDC_API_URL)
            if len(attempts) < 3:
                return usda_nutrition.httpx.Response(503, request=request)
            return usda_nutrition.httpx.Response(200, request=request, json={"foods": [_food(18, "Banana raw")]})

    monkeypatch.setattr(usda_nutrition.settings, "USDA_FDC_API_KEY", "test-key")
    monkeypatch.setattr(usda_nutrition.httpx, "Client", TransientClient)
    monkeypatch.setattr(usda_nutrition.time, "sleep", lambda _seconds: None)

    match = usda_nutrition.lookup_food("banana raw")

    assert len(attempts) == 3
    assert match.fdc_id == 18


def test_resolve_foods_deduplicates_queries_and_reuses_results(monkeypatch):
    calls = []
    match = FDCMatch(
        fdc_id=1,
        description="Bananas, raw",
        data_type="Foundation",
        nutrients_per_100g={"kcal": 89.0, "protein_g": 1.1, "carbs_g": 22.8, "fat_g": 0.3},
    )
    monkeypatch.setattr(usda_nutrition, "lookup_food", lambda query: calls.append(query) or match)

    resolved = resolve_foods(["Banana raw", "banana raw", " Banana raw "])

    assert calls == ["banana raw"]
    assert resolved["banana raw"] == match


def test_resolve_foods_bounds_nested_weekly_concurrency(monkeypatch):
    active = 0
    peak = 0
    lock = usda_nutrition.threading.Lock()

    def lookup(query):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        usda_nutrition.threading.Event().wait(0.01)
        with lock:
            active -= 1
        return FDCMatch(
            fdc_id=len(query),
            description=query,
            data_type="Foundation",
            nutrients_per_100g={"kcal": 1, "protein_g": 1, "carbs_g": 1, "fat_g": 1},
        )

    monkeypatch.setattr(usda_nutrition, "lookup_food", lookup)
    resolve_foods([f"food {index}" for index in range(12)])

    assert peak <= 2


def test_resolve_foods_stops_queued_lookups_when_usda_is_unavailable(monkeypatch):
    calls = []

    def unavailable(query):
        calls.append(query)
        raise USDAUnavailableError("temporarily unavailable")

    monkeypatch.setattr(usda_nutrition, "lookup_food", unavailable)

    resolved = resolve_foods([f"food {index}" for index in range(12)], max_workers=1)

    assert calls == ["food 0"]
    assert isinstance(resolved["food 0"], USDAUnavailableError)


@pytest.mark.parametrize("amount_g", [None, 0, -1, "not-a-number"])
def test_verify_ingredients_rejects_missing_or_invalid_weight(monkeypatch, amount_g):
    monkeypatch.setattr(usda_nutrition, "lookup_food", lambda query: pytest.fail("lookup should not run"))
    with pytest.raises(USDANutritionError):
        verify_ingredients([{"name": "banana", "amount_g": amount_g}])
