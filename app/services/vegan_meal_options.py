"""Curated meal options with nutrition pinned to the local USDA snapshot."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.usda_local import load_catalog
from app.services.usda_nutrition import FDCMatch, _nutrients, verify_ingredients

# Exact IDs avoid fuzzy matching to mixes or foods with a different preparation.
_FOODS = {
    "partially defatted sunflower seed flour": 170156,
    "chickpea flour": 174288,
    "hemp seeds": 170148,
    "pumpkin seeds": 170556,
    "sunflower seeds": 170562,
    "cooked lentils": 172421,
    "cooked black beans": 173735,
    "cooked chickpeas": 173757,
    "cooked white beans": 175203,
    "cooked kidney beans": 173740,
    "cooked pinto beans": 175200,
    "cooked split peas": 172429,
    "cooked quinoa": 168917,
    "cooked brown rice": 169704,
    "cooked millet": 168871,
    "cooked buckwheat groats": 170686,
    "cooked amaranth": 170683,
    "cooked potatoes": 170440,
    "cooked sweet potato": 168483,
    "zucchini": 169291,
    "red pepper": 170108,
    "broccoli": 170379,
    "carrots": 170393,
    "tomatoes": 170457,
    "banana": 173944,
    "blueberries": 171711,
    "strawberries": 167762,
    "raspberries": 167755,
    "apple": 171688,
    "pear": 169118,
    "mango": 169910,
    "peach": 169928,
    "pineapple": 169124,
    "lemon juice": 167747,
    "lime juice": 168156,
    "olive oil": 171413,
    "raw salmon": 175167,
    "raw cod": 171955,
    "raw tilapia": 175176,
}

# Fruit pair, lunch pulse and starch, dinner pulse and starch, two vegetables.
_MENUS = (
    ("blueberries", "pear", "lentils", "quinoa", "black beans", "brown rice", "zucchini", "red pepper"),
    ("strawberries", "apple", "chickpeas", "millet", "white beans", "sweet potato", "carrots", "tomatoes"),
    ("raspberries", "mango", "black beans", "buckwheat groats", "split peas", "amaranth", "broccoli", "carrots"),
    ("apple", "peach", "white beans", "potatoes", "kidney beans", "quinoa", "red pepper", "zucchini"),
    ("pear", "pineapple", "kidney beans", "brown rice", "pinto beans", "millet", "tomatoes", "broccoli"),
    ("mango", "blueberries", "pinto beans", "sweet potato", "lentils", "buckwheat groats", "zucchini", "carrots"),
    ("peach", "raspberries", "split peas", "amaranth", "chickpeas", "potatoes", "carrots", "red pepper"),
)

MENU_COUNT = len(_MENUS)
_SUNFLOWER_FLOUR = "partially defatted sunflower seed flour"


def _recipe(
    title: str,
    amounts: dict[str, int],
    steps: list[str],
    cook: int,
    *,
    protein: str = "",
    carb: str = "",
) -> dict[str, Any]:
    prep = 8 if cook else 5
    return {
        "title": title.title(),
        "ingredients": [
            {"name": name, "amount": grams, "amount_g": grams, "unit": "g", "usda_search_query": name}
            for name, grams in amounts.items()
        ],
        "instructions": steps,
        "prep_time_min": prep,
        "cook_time_min": cook,
        "total_time_min": prep + cook,
        "protein_group": "plant",
        "protein_item": protein,
        "carb_item": carb,
    }


def _menu(index: int) -> dict[str, dict[str, Any]]:
    fruit, snack_fruit, pulse, starch, second_pulse, second_starch, veg, second_veg = _MENUS[index % MENU_COUNT]
    # Offset the spread's legume from both the lunch and dinner legumes.
    _, _, snack_pulse, *_ = _MENUS[(index + 3) % MENU_COUNT]
    seeds = ("hemp seeds", "pumpkin seeds", "sunflower seeds")[index % 3]
    citrus = "lemon juice" if index % 2 == 0 else "lime juice"
    breakfast_format = "breakfast squares" if index % 3 == 1 else "griddle cakes"
    breakfast_step = (
        "Grease a small baking dish with the olive oil. Spread the batter in a thin layer and bake at 190°C (375°F) for 18–22 minutes until the center is set with no wet batter."
        if breakfast_format == "breakfast squares"
        else "Heat the olive oil in a skillet over medium heat. Make small, thin cakes and cook 3–4 minutes per side until the centers are set with no wet batter."
    )
    lunch_format = "skillet" if index % 3 == 2 else "patties"
    lunch_steps = (
        [
            f"Sauté the chopped {veg} in the olive oil for 4 minutes. Mash half of the cooked {pulse} and stir in the rest.",
            "Whisk the sunflower seed flour with water. Stir it into the skillet and cook over medium heat for 8 minutes, stirring often, until thickened and cooked through.",
            f"Fold in the cooked {starch}, warm through, and finish with {citrus}.",
        ]
        if lunch_format == "skillet"
        else [
            f"Mash the cooked {pulse}. Mix with finely chopped {veg} and the sunflower seed flour. Add a splash of water if needed, then shape into thin patties.",
            "Heat the olive oil in a skillet over medium heat. Cook the patties for 5–6 minutes per side until browned and firm throughout.",
            f"Warm the cooked {starch} separately and serve with the patties and {citrus}.",
        ]
    )
    return {
        "breakfast": _recipe(
            f"{fruit} and banana {breakfast_format} with {seeds}",
            {fruit: 95, "banana": 100, _SUNFLOWER_FLOUR: 42, "chickpea flour": 24, seeds: 15, "olive oil": 6},
            [
                "Mash the banana, then stir in the chickpea flour and sunflower seed flour. Add water a little at a time to make a thick batter.",
                breakfast_step,
                f"Serve with the {fruit} and {seeds}.",
            ],
            24 if breakfast_format == "breakfast squares" else 14,
            carb=fruit,
        ),
        "lunch": _recipe(
            f"{pulse} and {veg} {lunch_format} with {starch}",
            {
                f"cooked {pulse}": 145,
                f"cooked {starch}": 150,
                veg: 90,
                _SUNFLOWER_FLOUR: 35,
                "olive oil": 9,
                citrus: 12,
            },
            lunch_steps,
            16,
            protein=pulse,
            carb=starch,
        ),
        "dinner": _recipe(
            f"{second_pulse} and {second_veg} stew over {second_starch}",
            {
                f"cooked {second_pulse}": 165,
                f"cooked {second_starch}": 165,
                second_veg: 105,
                _SUNFLOWER_FLOUR: 35,
                "olive oil": 10,
                citrus: 12,
            },
            [
                f"Chop the {second_veg} and sauté in the olive oil over medium heat for 4 minutes.",
                f"Whisk the sunflower seed flour into water. Stir it into the pan with the cooked {second_pulse}. Bring to a gentle boil and simmer for 10 minutes, stirring often; add water if it thickens too much.",
                f"Warm the cooked {second_starch} separately. Spoon the stew over it and finish with {citrus}.",
            ],
            19,
            protein=second_pulse,
            carb=second_starch,
        ),
        "snack": _recipe(
            f"{snack_fruit} and {seeds} snack cup",
            {snack_fruit: 145, seeds: 38},
            [f"Cut the {snack_fruit} into bite-size pieces.", f"Top with the measured {seeds} and serve."],
            0,
            carb=snack_fruit,
        ),
        "snack_2": _recipe(
            f"{snack_pulse} and {citrus} spread with {second_veg}",
            {f"cooked {snack_pulse}": 95, second_veg: 100, "hemp seeds": 25, citrus: 12, "olive oil": 4},
            [
                f"Mash the cooked {snack_pulse} with the {citrus} and olive oil, then stir in the hemp seeds.",
                f"Slice the {second_veg} and serve with the spread.",
            ],
            0,
            carb=snack_pulse,
        ),
        "snack_3": _recipe(
            f"{second_pulse} and {veg} bowl with pumpkin seeds",
            {f"cooked {second_pulse}": 100, veg: 85, "pumpkin seeds": 24, citrus: 12},
            [
                f"Finely chop the {veg} and toss with the cooked {second_pulse}.",
                f"Add the measured pumpkin seeds and {citrus}, then serve.",
            ],
            0,
            carb=second_pulse,
        ),
    }


_FISH_DINNERS = {0: "salmon", 2: "cod", 4: "tilapia"}


def _fish_dinner(index: int, fish: str) -> dict[str, Any]:
    _, _, _, _, _, starch, _, veg = _MENUS[index]
    citrus = "lemon juice" if index % 2 == 0 else "lime juice"
    return _recipe(
        f"Roasted {fish} with {starch} and {veg}",
        {f"raw {fish}": 135, f"cooked {starch}": 230, veg: 130, "olive oil": 12, citrus: 12},
        [
            f"Heat the oven to 205°C (400°F). Cut the {veg} into small pieces and toss with half the olive oil.",
            f"Place the {veg} and {fish} on a baking tray. Brush the fish with the rest of the olive oil.",
            f"Roast until the {veg} is tender and the fish reaches an internal temperature of 145°F (63°C), about 12–18 minutes. Warm the cooked {starch} separately and serve with {citrus}.",
        ],
        20,
        protein=fish,
        carb=starch,
    ) | {"protein_group": "fish"}


def curated_menu_options(index: int, slots: list[str], *, include_fish: bool) -> dict[str, dict[str, Any]]:
    """Build a fresh menu using exact, local USDA evidence or decline it."""
    if not settings.USDA_LOCAL_CATALOG_ENABLED:
        return {}
    catalog = load_catalog(settings.USDA_LOCAL_CATALOG_PATH, settings.USDA_LOCAL_CATALOG_SHA256)
    if catalog is None:
        return {}

    menu = _menu(index)
    fish = _FISH_DINNERS.get(index % MENU_COUNT) if include_fish else None
    if fish:
        menu["dinner"] = _fish_dinner(index % MENU_COUNT, fish)
    if any(slot not in menu for slot in slots):
        return {}
    for slot in slots:
        recipe = menu[slot]
        resolved = {}
        for ingredient in recipe["ingredients"]:
            name = ingredient["name"]
            food = catalog.foods.get(_FOODS[name])
            values = _nutrients(food) if food else None
            if not values:
                return {}
            resolved[name] = FDCMatch(food["fdcId"], food["description"], food["dataType"], values)
        recipe["ingredients"] = verify_ingredients(recipe["ingredients"], resolved_foods=resolved)
    return {slot: menu[slot] for slot in slots}
