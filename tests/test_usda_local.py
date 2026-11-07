import hashlib
import json

import pytest

from app.services import usda_nutrition
from app.services.usda_local import LocalCatalog, load_catalog
from tests.test_usda_nutrition import _food


def test_local_matches_existing_selector_and_preserves_preparation():
    foods = [_food(1, "Chicken breast raw"), _food(2, "Chicken breast cooked")]
    catalog = LocalCatalog({"schema": 1, "release": "test", "foods": foods})
    for query in ["chicken breast raw", "chicken breast cooked"]:
        assert catalog.lookup(query) == usda_nutrition.select_match(query, foods)
    assert catalog.lookup("banana raw") is None


def test_ambiguity_falls_back_instead_of_arbitrary_id():
    a, b = _food(1, "Banana raw"), _food(2, "Banana raw")
    b["foodNutrients"][0]["value"] = 110
    b["foodNutrients"][2]["value"] = 28
    assert LocalCatalog({"schema": 1, "release": "test", "foods": [a, b]}).lookup("banana raw") is None


def test_checksum_failure_disables_snapshot(tmp_path):
    path = tmp_path / "foods.json"
    path.write_text(json.dumps({"schema": 1, "release": "test", "foods": [_food(1, "Banana raw")]}))
    assert load_catalog(str(path), "wrong") is None
    assert load_catalog(str(path), hashlib.sha256(path.read_bytes()).hexdigest()).lookup("banana raw").fdc_id == 1


def test_local_hit_needs_no_api_key_and_does_not_overwrite_verified_mapping(monkeypatch):
    match = usda_nutrition.select_match("banana raw", [_food(42, "Banana raw")])
    monkeypatch.setattr(usda_nutrition, "_catalog_match", lambda q: None)
    monkeypatch.setattr("app.services.usda_local.lookup_local", lambda q: match)
    monkeypatch.setattr(usda_nutrition.settings, "USDA_FDC_API_KEY", None)
    usda_nutrition.lookup_food.cache_clear()
    assert usda_nutrition.lookup_food("banana raw") == match
    assert usda_nutrition.resolve_foods(["banana raw"], max_live_lookups=0)["banana raw"] == match
    usda_nutrition.lookup_food.cache_clear()


def test_bundled_snapshot_integrity_and_no_nonfinite_nutrients():
    from app.config import settings

    catalog = load_catalog(settings.USDA_LOCAL_CATALOG_PATH, settings.USDA_LOCAL_CATALOG_SHA256)
    assert catalog is not None
    assert len(catalog.foods) > 13000
    assert {"Foundation", "SR Legacy", "Survey (FNDDS)"} == {f["dataType"] for f in catalog.foods.values()}


def test_disabled_catalog_skips_loader(monkeypatch):
    from app.config import settings
    from app.services import usda_local

    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", False)
    monkeypatch.setattr(usda_local, "load_catalog", lambda *args: pytest.fail("disabled catalog loaded"))
    assert usda_local.lookup_local("banana") is None


def test_snapshot_import_retains_usda_values_and_excludes_invalid(tmp_path):
    from scripts.usda.import_snapshot import compile_snapshot

    food = _food(1, "Banana raw")
    records = []
    for key, amount in [(1, 89), (2, -1)]:
        records.append(
            {
                "fdcId": key,
                "description": food["description"],
                "dataType": food["dataType"],
                "foodNutrients": [
                    {"nutrient": {"id": n["nutrientId"]}, "amount": amount if n["nutrientId"] == 1008 else n["value"]}
                    for n in food["foodNutrients"]
                ],
            }
        )
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"FoundationFoods": records}))
    snapshot = compile_snapshot([path], "fixture")
    assert snapshot["excluded_incomplete"] == 1
    assert snapshot["foods"] == [food]
    assert snapshot["sources"][0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_local_miss_uses_existing_api_fallback(monkeypatch):
    import httpx

    match_food = _food(123, "Banana raw")
    monkeypatch.setattr(usda_nutrition, "_catalog_match", lambda q: None)
    monkeypatch.setattr("app.services.usda_local.lookup_local", lambda q: None)
    monkeypatch.setattr(usda_nutrition.settings, "USDA_FDC_API_KEY", "synthetic-key")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"foods": [match_food]}))
    client = httpx.Client(transport=transport)
    monkeypatch.setattr(usda_nutrition.httpx, "Client", lambda **kw: client)
    stored = []
    monkeypatch.setattr(usda_nutrition, "_store_catalog_match", lambda q, m: stored.append(m))
    usda_nutrition.lookup_food.cache_clear()
    assert usda_nutrition.lookup_food("banana raw").fdc_id == 123
    assert len(stored) == 1
    usda_nutrition.lookup_food.cache_clear()


def test_equivalent_ties_still_use_api_relevance_order():
    foods = [_food(1, "Banana raw"), _food(2, "Banana raw")]
    catalog = LocalCatalog({"schema": 1, "release": "test", "foods": foods})
    assert catalog.lookup("banana raw") is None


@pytest.mark.parametrize("query", ["milk whole", "whole milk", "whole milk plain"])
def test_bundled_whole_milk_is_fluid_milk_not_yogurt(query):
    from app.config import settings

    catalog = load_catalog(settings.USDA_LOCAL_CATALOG_PATH, settings.USDA_LOCAL_CATALOG_SHA256)
    match = catalog.lookup(query)
    assert match is not None
    assert match.description == "Milk, whole"
    assert match.nutrients_per_100g == {"kcal": 61, "protein_g": 3.27, "carbs_g": 4.63, "fat_g": 3.2}
