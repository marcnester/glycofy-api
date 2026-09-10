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


@pytest.mark.parametrize("amount_g", [None, 0, -1, "not-a-number"])
def test_verify_ingredients_rejects_missing_or_invalid_weight(monkeypatch, amount_g):
    monkeypatch.setattr(usda_nutrition, "lookup_food", lambda query: pytest.fail("lookup should not run"))
    with pytest.raises(USDANutritionError):
        verify_ingredients([{"name": "banana", "amount_g": amount_g}])
