from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services import weekly_prompt as prompt
from app.services.meal_quality import PROMPT_VERSION, violates_diet, violates_exclusions


def cache_row(query, description, nutrients, fdc_id):
    return {
        "query": query,
        "description": description,
        "dataType": "SR Legacy",
        "fdc_id": fdc_id,
        "nutrients": dict(zip(("kcal", "protein_g", "carbs_g", "fat_g"), nutrients, strict=True)),
    }


@pytest.fixture
def reference_rows():
    return [
        cache_row("lentils cooked", "Lentils, mature seeds, cooked, boiled, without salt", (116, 9, 20, 0.4), 1),
        cache_row("greek yogurt plain nonfat", "Yogurt, Greek, plain, nonfat", (59, 10, 3.6, 0.4), 2),
        cache_row("bread whole wheat", "Bread, whole wheat", (253, 9, 43, 3), 3),
        cache_row("oats raw", "Oats, raw", (389, 17, 66, 7), 4),
        cache_row("banana raw", "Bananas, raw", (89, 1.1, 22.8, 0.3), 5),
        cache_row("olive oil", "Oil, olive, salad or cooking", (884, 0, 0, 100), 6),
        cache_row("milk low fat", "Milk, low fat, fluid", (42, 3.4, 5, 1), 7),
        # A historical bad mapping must not become prompt reference data.
        cache_row("apple raw with skin", "Apricots, raw", (48, 1.4, 11, 0.4), 8),
    ]


@pytest.mark.parametrize("diet, exclusions", [("omnivore", []), ("omnivore", ["milk"]), ("vegan", ["soy", "wheat"])])
def test_reference_foods_retain_verified_query_and_respect_restrictions(reference_rows, diet, exclusions):
    options = prompt.verified_ingredient_options(
        diet=diet,
        exclusions=exclusions,
        diet_tags=[],
        catalog=SimpleNamespace(lookup=lambda _: None),
        cache_rows=reference_rows,
    )
    assert options
    by_query = {r["query"]: r for r in reference_rows}
    for option in options:
        row = by_query[option["usda_search_query"]]
        assert option["name"] == row["description"]
        assert option["per_100g"] == row["nutrients"]
        assert option["min_g"] < option["max_g"]
        identity = {"ingredients": [{"name": option["name"], "usda_search_query": option["usda_search_query"]}]}
        assert not violates_diet(identity, diet)
        assert not violates_exclusions(identity, exclusions)
    assert all(o["usda_search_query"] != "apple raw with skin" for o in options)
    assert any(o["role"] == "protein" for o in options)


def test_small_library_preserves_food_roles_instead_of_filling_with_protein(reference_rows):
    options = prompt.verified_ingredient_options(
        diet="omnivore",
        exclusions=[],
        diet_tags=[],
        catalog=SimpleNamespace(lookup=lambda _: None),
        cache_rows=reference_rows,
        limit=4,
    )
    assert {o["role"] for o in options} == {"protein", "starch", "produce", "fat"}


def test_reference_data_does_not_change_targets_training_or_contract(reference_rows):
    base = "Hard exclusions. Exact slots. Never alter verified nutrition or training targets."
    payload = {
        "primary_diet": "vegan",
        "days": [
            {
                "date": "2026-11-20",
                "meals": [{"slot": "snack_2", "target_macros": {"carbs_g": 40}}],
                "training": {"during_carbs_g": 60},
            }
        ],
        "forbidden_titles": ["Repeat"],
    }
    original = deepcopy(payload)
    options = prompt.verified_ingredient_options(
        diet="vegan",
        exclusions=[],
        diet_tags=[],
        catalog=SimpleNamespace(lookup=lambda _: None),
        cache_rows=reference_rows,
    )
    system, updated = prompt.evidence_first_prompt(base, payload, options)
    assert system.startswith(base)
    assert payload == original
    assert {k: v for k, v in updated.items() if k != "verified_ingredient_options"} == original
    assert updated["verified_ingredient_options"] == options


def test_unsupported_restrictions_and_missing_protein_keep_original_prompt(reference_rows):
    assert (
        prompt.verified_ingredient_options(
            diet="vegan",
            exclusions=[],
            diet_tags=["low_fodmap"],
            catalog=SimpleNamespace(lookup=lambda _: pytest.fail("secondary restriction must not query foods")),
            cache_rows=reference_rows,
        )
        == []
    )
    assert prompt.evidence_first_prompt("Original", {"days": []}, []) == ("Original", {"days": []})
    assert (
        prompt.verified_ingredient_options(
            diet="vegan",
            exclusions=[],
            diet_tags=[],
            catalog=SimpleNamespace(lookup=lambda _: None),
            cache_rows=[reference_rows[1]],
        )
        == []
    )


@pytest.mark.parametrize("value", [None, "control", "typo", "EVIDENCE_FIRST"])
def test_current_prompt_remains_default_and_unknown_variants_are_inactive(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("WEEKLY_PROMPT_VARIANT", raising=False)
    else:
        monkeypatch.setenv("WEEKLY_PROMPT_VARIANT", value)
    assert not prompt.candidate_enabled()
    assert prompt.weekly_prompt_version() == PROMPT_VERSION


def test_preparation_words_cannot_turn_an_unrelated_food_into_protein(reference_rows):
    # Sharing "raw" with cod must not make oats a fish/protein reference.
    rows = [r for r in reference_rows if r["query"] in {"oats raw", "banana raw", "olive oil"}]
    assert (
        prompt.verified_ingredient_options(
            diet="omnivore",
            exclusions=[],
            diet_tags=[],
            catalog=SimpleNamespace(lookup=lambda _: None),
            cache_rows=rows,
        )
        == []
    )


def test_reference_choice_does_not_depend_on_cache_row_order(reference_rows):
    rows = [r for r in reference_rows if r["query"] != "banana raw"] + [
        cache_row("raw banana", "Bananas, raw", (89, 1.1, 22.8, 0.3), 55),
        cache_row("banana fresh raw", "Bananas, raw", (89, 1.1, 22.8, 0.3), 56),
    ]
    kwargs = {"diet": "omnivore", "exclusions": [], "diet_tags": [], "catalog": SimpleNamespace(lookup=lambda _: None)}
    assert prompt.verified_ingredient_options(cache_rows=rows, **kwargs) == prompt.verified_ingredient_options(
        cache_rows=list(reversed(rows)), **kwargs
    )
