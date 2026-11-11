"""Exclusion-checked examples and coordinated ingredient choices for weekly AI planning."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.meal_quality import ingredient_nutrition_totals, validate_meal, violates_diet, violates_exclusions
from app.services.usda_local import load_catalog
from app.services.usda_nutrition import _nutrients, fit_portions_to_targets
from app.services.vegan_meal_options import MENU_COUNT, curated_menu_options

# These names and preparation states refer to exact entries in the local snapshot.
_PROTEINS = (
    ("raw egg whites", 172183),
    ("plain nonfat Greek yogurt", 170894),
    ("raw turkey breast", 174515),
    ("raw salmon", 175167),
    ("raw cod", 171955),
    ("raw tilapia", 175176),
    ("firm tofu", 172475),
    ("cooked lentils", 172421),
    ("cooked chickpeas", 173757),
    ("cooked black beans", 173735),
    ("cooked white beans", 175203),
    ("cooked kidney beans", 173740),
    ("cooked pinto beans", 175200),
    ("cooked split peas", 172429),
)
_STARCHES = (
    ("cooked potatoes", 170440),
    ("cooked quinoa", 168917),
    ("cooked brown rice", 169704),
    ("cooked millet", 168871),
    ("cooked sweet potato", 168483),
    ("cooked buckwheat groats", 170686),
    ("cooked amaranth", 170683),
)
_MAIN_SLOTS = ("breakfast", "lunch", "dinner")


def _safe_foods(
    catalog_foods: dict[int, dict[str, Any]], foods: tuple[tuple[str, int], ...], diet: str, exclusions: list[str]
) -> dict[str, dict[str, Any]]:
    result = {}
    for name, fdc_id in foods:
        food = catalog_foods.get(fdc_id)
        if food is None:
            continue
        identity = {"ingredients": [{"name": name, "usda_search_query": food["description"]}]}
        if violates_diet(identity, diet) or violates_exclusions(identity, exclusions):
            continue
        values = _nutrients(food)
        if values:
            result[name] = {
                "usda_search_query": food["description"],
                "per_100g": {key: round(value, 1) for key, value in values.items()},
            }
    return result


def _checked_example(
    menu: dict[str, Any], slot: str, target: dict[str, Any], diet: str, exclusions: list[str]
) -> dict[str, Any] | None:
    original = menu["ingredients"]
    for ingredients in (fit_portions_to_targets(original, target), original):
        macros = ingredient_nutrition_totals({"ingredients": ingredients})
        if not macros:
            continue
        recipe = {**menu, "slot": slot, "ingredients": ingredients, "macros": macros}
        if validate_meal(recipe, diet=diet, exclusions=exclusions, require_ingredient_nutrition=True).safe:
            return {
                "format_example": menu["title"],
                "ingredients_g": {item["name"]: item["amount_g"] for item in ingredients},
                "estimated_meal_macros": macros,
            }
    return None


def guide_week_days(days: list[dict[str, Any]], *, diet: str, exclusions: list[str]) -> list[dict[str, Any]]:
    """Attach compact, optional starters without changing the existing targets."""
    if not settings.USDA_LOCAL_CATALOG_ENABLED:
        return days
    catalog = load_catalog(settings.USDA_LOCAL_CATALOG_PATH, settings.USDA_LOCAL_CATALOG_SHA256)
    if catalog is None:
        return days

    proteins = _safe_foods(catalog.foods, _PROTEINS, diet, exclusions)
    starches = _safe_foods(catalog.foods, _STARCHES, diet, exclusions)
    protein_names, starch_names = list(proteins), list(starches)
    result = []
    for index, day in enumerate(days):
        # Secondary dietary tags are not modeled by the starter library.
        if day.get("diet_tags"):
            result.append(day)
            continue
        slots = [meal["slot"] for meal in day.get("meals", [])]
        examples = [
            curated_menu_options((index + offset) % MENU_COUNT, slots, include_fish=diet == "pescatarian")
            for offset in (0, 3)
        ]
        starters = {}
        anchors = {}
        foods = {}
        for meal in day.get("meals", []):
            slot = meal["slot"]
            options = [
                option
                for menu in examples
                if slot in menu
                and (option := _checked_example(menu[slot], slot, meal["target_macros"], diet, exclusions)) is not None
            ]
            if slot in _MAIN_SLOTS:
                slot_offset = _MAIN_SLOTS.index(slot) * 2
                pairing = {}
                if len(protein_names) >= 5:
                    name = protein_names[(index + slot_offset) % len(protein_names)]
                    pairing["protein"] = name
                    foods[name] = proteins[name]
                if len(starch_names) >= 5:
                    name = starch_names[(index + slot_offset) % len(starch_names)]
                    pairing["carb"] = name
                    foods[name] = starches[name]
                if pairing:
                    anchors[slot] = pairing
            if options:
                starters[slot] = options

        if starters or anchors:
            result.append(
                {**day, "starter_examples": starters, "planned_anchors": anchors, "verified_anchor_foods": foods}
            )
        else:
            result.append(day)
    return result
