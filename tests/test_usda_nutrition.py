from __future__ import annotations

import pytest

from app.services import usda_nutrition
from app.services.usda_nutrition import FDCMatch, USDANutritionError, resolve_foods, select_match, verify_ingredients


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
    with pytest.raises(USDANutritionError, match="Ambiguous"):
        select_match(
            "banana raw",
            [
                _food(10, "Banana raw yellow"),
                _food(11, "Banana raw green"),
            ],
        )


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


@pytest.mark.parametrize("amount_g", [None, 0, -1, "not-a-number"])
def test_verify_ingredients_rejects_missing_or_invalid_weight(monkeypatch, amount_g):
    monkeypatch.setattr(usda_nutrition, "lookup_food", lambda query: pytest.fail("lookup should not run"))
    with pytest.raises(USDANutritionError):
        verify_ingredients([{"name": "banana", "amount_g": amount_g}])
