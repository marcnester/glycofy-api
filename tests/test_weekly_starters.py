from collections import Counter
from datetime import date, timedelta

from app.config import settings
from app.routers import llm_recommend
from app.services.meal_quality import text_violates_exclusions
from app.services.weekly_starters import guide_week_days


def _week():
    targets = {"kcal": 500, "protein_g": 35, "carbs_g": 55, "fat_g": 15}
    slots = ("breakfast", "lunch", "dinner", "snack", "snack_2")
    return [
        {
            "date": (date(2026, 12, 7) + timedelta(days=offset)).isoformat(),
            "meals": [{"slot": slot, "target_macros": dict(targets)} for slot in slots],
        }
        for offset in range(7)
    ]


def test_guidance_checks_recipe_options_and_coordinates_main_meal_anchors(monkeypatch):
    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", True)
    original = _week()
    exclusions = ["milk", "egg", "sesame", "wheat", "soy"]
    guided = guide_week_days(original, diet="pescatarian", exclusions=exclusions)

    assert all("starter_examples" not in day for day in original)
    for day in guided:
        assert set(day["starter_examples"]) == {"breakfast", "lunch", "dinner", "snack", "snack_2"}
        anchors = day["planned_anchors"].values()
        assert len({anchor["protein"] for anchor in anchors}) == 3
        assert len({anchor["carb"] for anchor in anchors}) == 3
        assert not text_violates_exclusions(str(day["starter_examples"]), exclusions)
        assert not text_violates_exclusions(str(day["verified_anchor_foods"]), exclusions)
        assert all(food["per_100g"]["kcal"] > 0 for food in day["verified_anchor_foods"].values())
        assert all(
            example["estimated_meal_macros"]["kcal"] > 0 and all(g > 0 for g in example["ingredients_g"].values())
            for examples in day["starter_examples"].values()
            for example in examples
        )

    for slot in ("breakfast", "lunch", "dinner"):
        for role in ("protein", "carb"):
            counts = Counter(day["planned_anchors"][slot][role] for day in guided)
            assert max(counts.values()) <= 2


def test_guidance_skips_secondary_diet_tags_it_does_not_model(monkeypatch):
    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", True)
    day = {**_week()[0], "diet_tags": ["low_fodmap"]}
    assert guide_week_days([day], diet="vegan", exclusions=[]) == [day]


def test_parallel_generation_passes_opt_in_guidance_without_conflicting_formats(monkeypatch):
    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", True)
    days = _week()[:2]
    calls = []

    def capture(_client, **kwargs):
        calls.append(kwargs)
        return {}, {}

    monkeypatch.setattr(llm_recommend, "_batch_week_recommendations", capture)
    kwargs = {"days": days, "primary_diet": "omnivore", "diet_tags": [], "exclusions": ["milk"]}
    monkeypatch.delenv("WEEKLY_GUIDED_FIRST_PASS_ENABLED", raising=False)
    llm_recommend._parallel_week_recommendations(object(), **kwargs)
    assert all("starter_examples" not in call["days"][0] for call in calls)
    assert all("breakfast format" in call["days"][0]["variety_assignment"] for call in calls)

    calls.clear()
    monkeypatch.setenv("WEEKLY_GUIDED_FIRST_PASS_ENABLED", "1")
    llm_recommend._parallel_week_recommendations(object(), **kwargs)
    assert len(calls) == 2
    for call in calls:
        day = call["days"][0]
        assert "breakfast" in day["starter_examples"]
        assert "breakfast format" not in day["variety_assignment"]
        assert all(context.get("planned_anchors") for context in call["week_context"])
        assert day["meals"] == next(item["meals"] for item in days if item["date"] == day["date"])
