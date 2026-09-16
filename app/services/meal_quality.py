from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PROMPT_VERSION = "meal-planner-2026-09-13-v11-beta-quality"
QUALITY_POLICY_VERSION = "nutrition-safety-2026-09-16-v20-practical-portions"

MACROS = ("kcal", "protein_g", "carbs_g", "fat_g")
SUPPORTED_DIETS = ("omnivore", "pescatarian", "vegetarian", "vegan")
SUPPORTED_ALLERGENS = ("milk", "egg", "fish", "shellfish", "tree_nuts", "peanut", "wheat", "soy", "sesame")
TERRESTRIAL_MEAT = {
    "bacon",
    "beef",
    "bison",
    "buffalo",
    "chicken",
    "duck",
    "goat",
    "ham",
    "lamb",
    "pork",
    "prosciutto",
    "rabbit",
    "sausage",
    "steak",
    "turkey",
    "veal",
    "venison",
}
SEAFOOD = {
    "anchovy",
    "clam",
    "cod",
    "crab",
    "crayfish",
    "fish",
    "lobster",
    "mussel",
    "oyster",
    "prawn",
    "salmon",
    "sardine",
    "scallop",
    "shellfish",
    "shrimp",
    "tilapia",
    "trout",
    "tuna",
}
ANIMAL_MEAT = TERRESTRIAL_MEAT | SEAFOOD
DAIRY_MARKERS = {
    "butter",
    "buttermilk",
    "casein",
    "cheddar",
    "cheese",
    "cream",
    "creme fraiche",
    "feta",
    "ghee",
    "kefir",
    "mascarpone",
    "milk",
    "mozzarella",
    "parmesan",
    "ricotta",
    "whey",
    "yoghurt",
    "yogurt",
}
ANIMAL_PRODUCTS = (
    ANIMAL_MEAT
    | DAIRY_MARKERS
    | {
        "egg",
        "eggs",
        "gelatin",
        "honey",
        "lard",
        "mayonnaise",
        "meringue",
    }
)
MEAT = TERRESTRIAL_MEAT
ALLERGEN_ALIASES = {
    "milk": DAIRY_MARKERS,
    "dairy": DAIRY_MARKERS,
    "lactose_intolerant": DAIRY_MARKERS - {"casein"},
    "milk_allergy": DAIRY_MARKERS,
    "dairy_allergy": DAIRY_MARKERS,
    "egg": {"egg", "eggs", "mayonnaise", "meringue"},
    "fish": {"anchovy", "cod", "fish", "salmon", "sardine", "tilapia", "trout", "tuna"},
    "shellfish": {"clam", "crab", "crayfish", "lobster", "mussel", "oyster", "prawn", "scallop", "shellfish", "shrimp"},
    "peanut": {"groundnut", "peanut", "peanuts"},
    "tree_nuts": {
        "almond",
        "brazil nut",
        "cashew",
        "hazelnut",
        "macadamia",
        "pecan",
        "pistachio",
        "tree nut",
        "walnut",
    },
    "soy": {"edamame", "miso", "soy", "soya", "tempeh", "tofu"},
    "sesame": {"sesame", "sesame seed", "tahini"},
    "wheat": {"bread", "bulgur", "couscous", "farro", "flour", "pasta", "seitan", "wheat"},
}
PLANT_DAIRY_PHRASES = {
    "almond milk",
    "cashew milk",
    "coconut cream",
    "coconut milk",
    "dairy free butter",
    "dairy free cheese",
    "dairy free milk",
    "dairy free yogurt",
    "hemp milk",
    "non dairy milk",
    "oat cream",
    "oat milk",
    "plant based butter",
    "plant based cheese",
    "plant based milk",
    "plant based yogurt",
    "plant milk",
    "rice milk",
    "soy cream",
    "soy milk",
    "vegan butter",
    "vegan cheese",
    "vegan mayonnaise",
    "vegan yogurt",
}
RAW_PROTEIN_MARKERS = ANIMAL_MEAT | {"egg", "eggs"}
READY_TO_EAT_PROTEIN_MARKERS = {
    "canned",
    "cooked",
    "deli",
    "hard boiled",
    "leftover",
    "precooked",
    "ready to eat",
    "roasted",
    "rotisserie",
    "smoked",
}
GROUND_MEAT_MARKERS = {
    "ground beef",
    "ground lamb",
    "ground pork",
    "ground veal",
    "minced beef",
    "minced lamb",
    "minced pork",
    "minced veal",
}
DONENESS_MARKERS = {
    "internal temperature",
    "opaque",
    "flakes easily",
    "no longer pink",
    "cooked through",
    "firm white",
    "until set",
    "°f",
    "°c",
}
NONFOOD_HAZARDS = {"bleach", "borax", "detergent", "dish soap", "rubbing alcohol"}
PROTEIN_SOURCE_MARKERS = {
    "beef",
    "chicken",
    "cod",
    "cottage cheese",
    "edamame",
    "egg",
    "eggs",
    "greek yogurt",
    "lentil",
    "lentils",
    "pork",
    "protein",
    "salmon",
    "seitan",
    "shrimp",
    "tempeh",
    "tofu",
    "tuna",
    "turkey",
    "whey",
}
CARB_SOURCE_MARKERS = {
    "bagel",
    "banana",
    "barley",
    "bean",
    "beans",
    "bread",
    "carb",
    "couscous",
    "fruit",
    "granola",
    "honey",
    "lentil",
    "oat",
    "oats",
    "pasta",
    "pita",
    "plantain",
    "potato",
    "quinoa",
    "rice",
    "tortilla",
    "tortillas",
    "wrap",
}
FAT_SOURCE_MARKERS = {
    "almond",
    "avocado",
    "butter",
    "cheese",
    "chia",
    "coconut",
    "egg",
    "oil",
    "peanut",
    "salmon",
    "seed",
    "seeds",
    "tahini",
    "walnut",
}
SEASONING_MARKERS = {
    "cardamom",
    "coriander",
    "cumin",
    "curry powder",
    "garam masala",
    "harissa",
    "oregano",
    "paprika",
    "rosemary",
    "thyme",
    "turmeric",
    "zaatar",
}
SMALL_AMOUNT_EXEMPTIONS = SEASONING_MARKERS | {
    "basil",
    "cinnamon",
    "garlic",
    "lemon juice",
    "lime juice",
    "parsley",
    "pepper",
    "salt",
    "vinegar",
}


def practical_minimum_grams(name: Any, query: Any = None) -> float:
    """Return a conservative minimum portion that remains useful to a cook."""
    identity = _words(f"{name or ''} {query or ''}")
    if any(_contains(identity, marker) for marker in SMALL_AMOUNT_EXEMPTIONS):
        return 0.5
    if any(_contains(identity, marker) for marker in ("olive oil", "sesame oil", "cooking oil")):
        return 3.0
    if any(_contains(identity, marker) for marker in ("almond butter", "peanut butter", "tahini")):
        return 8.0
    if any(_contains(identity, marker) for marker in ("nut", "seed")):
        return 5.0
    if any(_contains(identity, marker) for marker in ("greek yogurt", "yogurt", "cottage cheese", "ricotta", "skyr")):
        return 75.0
    if any(
        _contains(identity, marker)
        for marker in (
            "chicken",
            "turkey",
            "beef",
            "pork",
            "lamb",
            "salmon",
            "cod",
            "tuna",
            "tilapia",
            "trout",
            "tofu",
            "tempeh",
        )
    ):
        return 50.0
    if _contains(identity, "egg"):
        return 50.0
    if _contains(identity, "oat"):
        return 15.0
    if any(_contains(identity, marker) for marker in ("bread", "pita", "tortilla", "wrap")):
        return 25.0
    if any(
        _contains(identity, marker)
        for marker in (
            "apple",
            "banana",
            "berries",
            "berry",
            "grape",
            "orange",
            "pear",
            "pineapple",
            "mango",
            "peach",
        )
    ):
        return 30.0
    if any(
        _contains(identity, marker)
        for marker in (
            "broccoli",
            "carrot",
            "cucumber",
            "zucchini",
            "bell pepper",
            "bok choy",
            "spinach",
            "cauliflower",
            "asparagus",
            "green bean",
            "tomato",
        )
    ):
        return 30.0
    if any(_contains(identity, marker) for marker in ("hummus", "lentil", "bean", "chickpea", "edamame")):
        return 30.0
    return 5.0


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass
class MealQualityReport:
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def ingredient_nutrition_totals(meal: dict[str, Any]) -> dict[str, float] | None:
    """Sum itemized nutrition evidence, or return None when any ingredient lacks it."""
    ingredients = meal.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        return None
    totals = {name: 0.0 for name in MACROS}
    for item in ingredients:
        if not isinstance(item, dict):
            return None
        nutrition = item.get("nutrition")
        if not isinstance(nutrition, dict):
            return None
        values = {name: _number(nutrition.get(name)) for name in MACROS}
        if any(value is None for value in values.values()):
            return None
        for name, value in values.items():
            totals[name] += float(value or 0.0)
    return {name: round(value, 1) for name, value in totals.items()}


def _words(value: Any) -> str:
    return re.sub(r"[^a-z0-9°]+", " ", str(value or "").lower()).strip()


def _contains(text: str, marker: str) -> bool:
    forms = {marker}
    if marker.endswith("s") and len(marker) > 1:
        forms.add(marker[:-1])
    else:
        forms.update({f"{marker}s", f"{marker}es"})
    return any(bool(re.search(rf"\b{re.escape(form)}\b", text)) for form in forms)


def _without_plant_dairy_phrases(text: str) -> str:
    """Remove explicitly plant-based dairy alternatives before dairy checks."""
    cleaned = text
    for phrase in sorted(PLANT_DAIRY_PHRASES, key=len, reverse=True):
        cleaned = re.sub(rf"\b{re.escape(phrase)}\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def recipe_text(meal: dict[str, Any]) -> str:
    """Flatten every model-controlled recipe field used for safety checks."""
    fields = (
        meal.get("title", ""),
        meal.get("ingredients") or [],
        meal.get("instructions") or [],
        meal.get("protein_item", ""),
        meal.get("carb_item", ""),
    )
    return _words(" ".join(json.dumps(value, default=str) for value in fields))


def ingredient_text(meal: dict[str, Any]) -> str:
    """Backward-compatible name for the complete safety-relevant recipe text."""
    return recipe_text(meal)


def _has_raw_animal_protein(meal: dict[str, Any]) -> bool:
    """Return whether a listed animal protein still needs to be cooked."""
    ingredients = meal.get("ingredients")
    if not isinstance(ingredients, list):
        return False
    for item in ingredients:
        if not isinstance(item, dict):
            continue
        name = _words(item.get("name"))
        if not any(_contains(name, marker) for marker in RAW_PROTEIN_MARKERS):
            continue
        if any(_contains(name, marker) for marker in READY_TO_EAT_PROTEIN_MARKERS):
            continue
        return True
    return False


def ensure_safe_doneness_instruction(meal: dict[str, Any]) -> dict[str, Any]:
    """Add a deterministic food-safety cue when an otherwise usable recipe omits one."""
    instructions = meal.get("instructions")
    cook = _number(meal.get("cook_time_min"))
    if not isinstance(instructions, list) or not cook or cook <= 0:
        return meal

    instruction_text = _words(" ".join(str(step) for step in instructions))
    if not _has_raw_animal_protein(meal) or any(marker in instruction_text for marker in DONENESS_MARKERS):
        return meal

    text = ingredient_text(meal)

    if any(_contains(text, marker) for marker in GROUND_MEAT_MARKERS):
        cue = "Cook ground meat until the internal temperature reaches 160°F (71°C)."
    elif any(_contains(text, marker) for marker in ("chicken", "turkey")):
        cue = "Cook until no longer pink and the internal temperature reaches 165°F."
    elif any(_contains(text, marker) for marker in ("egg", "eggs")):
        cue = "Cook until the eggs are fully set and the internal temperature reaches 160°F."
    elif any(_contains(text, marker) for marker in ("cod", "fish", "salmon", "shrimp", "tuna")):
        cue = "Cook until opaque and the internal temperature reaches 145°F."
    else:
        cue = "Cook until the internal temperature reaches 145°F, then rest for 3 minutes."
    meal["instructions"] = [*instructions, cue]
    return meal


def violates_exclusions(meal: dict[str, Any], exclusions: list[str]) -> list[str]:
    return text_violates_exclusions(ingredient_text(meal), exclusions)


def text_violates_exclusions(text: str, exclusions: list[str]) -> list[str]:
    """Return excluded foods found in normalized recipe or catalog text."""
    normalized_text = _words(text)
    hits: list[str] = []
    for raw in exclusions:
        exclusion = _words(raw)
        aliases = ALLERGEN_ALIASES.get(exclusion.replace(" ", "_"), {exclusion})
        searchable = (
            _without_plant_dairy_phrases(normalized_text)
            if exclusion.replace(" ", "_") in {"milk", "dairy", "lactose_intolerant", "milk_allergy", "dairy_allergy"}
            else normalized_text
        )
        if any(_contains(searchable, alias) for alias in aliases if alias):
            hits.append(raw)
    return hits


def violates_diet(meal: dict[str, Any], diet: str | None) -> list[str]:
    text = ingredient_text(meal)
    normalized = _words(diet)
    prohibited: set[str] = set()
    if normalized == "vegan":
        prohibited = ANIMAL_PRODUCTS
    elif normalized == "vegetarian":
        prohibited = ANIMAL_MEAT
    elif normalized == "pescatarian":
        prohibited = MEAT
    searchable = _without_plant_dairy_phrases(text) if normalized == "vegan" else text
    return sorted(marker for marker in prohibited if _contains(searchable, marker))


def validate_meal(
    meal: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    exclusions: list[str] | None = None,
    diet: str | None = None,
    require_ingredient_nutrition: bool = False,
    target_miss_severity: str = "error",
) -> MealQualityReport:
    report = MealQualityReport()
    title = str(meal.get("title") or "").strip()
    ingredients = meal.get("ingredients")
    instructions = meal.get("instructions")
    macros = meal.get("macros") or meal.get("macro_estimate") or meal.get("approx_macros") or {}

    if len(title) < 3:
        report.issues.append(QualityIssue("missing_title", "Meal title is missing."))
    if not isinstance(ingredients, list) or len(ingredients) < 2:
        report.issues.append(QualityIssue("incomplete_ingredients", "At least two measured ingredients are required."))
    else:
        for item in ingredients:
            if (
                not isinstance(item, dict)
                or not str(item.get("name") or "").strip()
                or item.get("amount", item.get("qty")) in (None, "")
                or not str(item.get("unit") or "").strip()
            ):
                report.issues.append(
                    QualityIssue("unmeasured_ingredient", "Every ingredient needs a name, amount, and unit.")
                )
                break

    evidence = ingredient_nutrition_totals(meal)
    if require_ingredient_nutrition and evidence is None:
        report.issues.append(
            QualityIssue(
                "missing_ingredient_nutrition",
                "Every ingredient needs an itemized calorie and macronutrient contribution.",
            )
        )
    if not isinstance(instructions, list) or len([step for step in instructions if str(step).strip()]) < 2:
        report.issues.append(QualityIssue("incomplete_instructions", "At least two preparation steps are required."))

    ingredient_words = _words(
        " ".join(str(item.get("name") or "") for item in ingredients or [] if isinstance(item, dict))
    )
    instruction_words = _words(" ".join(str(step) for step in instructions or []))
    unlisted_seasonings = sorted(
        marker
        for marker in SEASONING_MARKERS
        if _contains(instruction_words, marker) and not _contains(ingredient_words, marker)
    )
    if unlisted_seasonings:
        report.issues.append(
            QualityIssue(
                "unlisted_instruction_ingredient",
                f"Instructions use ingredients that are not listed: {', '.join(unlisted_seasonings)}.",
            )
        )

    if isinstance(ingredients, list):
        for item in ingredients:
            if not isinstance(item, dict):
                continue
            name = _words(item.get("name"))
            amount = _number(item.get("amount_g", item.get("amount", item.get("qty"))))
            unit = _words(item.get("unit"))
            if amount is None or unit not in {"g", "gram", "grams"}:
                continue
            practical_minimum = practical_minimum_grams(name, item.get("usda_search_query"))
            if amount < practical_minimum:
                report.issues.append(
                    QualityIssue(
                        "impractical_serving",
                        f"{item.get('name')} has an impractically small serving.",
                    )
                )
                break

        slot = _words(meal.get("slot"))
        protein_item = _words(meal.get("protein_item"))
        if slot in {"breakfast", "lunch", "dinner"} and protein_item:
            for item in ingredients:
                if not isinstance(item, dict):
                    continue
                name = _words(item.get("name"))
                amount = _number(item.get("amount_g", item.get("amount", item.get("qty"))))
                unit = _words(item.get("unit"))
                if protein_item in name and unit in {"g", "gram", "grams"} and amount is not None and amount < 75:
                    report.issues.append(
                        QualityIssue(
                            "impractical_primary_protein",
                            f"{item.get('name')} is too small to serve as the main protein.",
                        )
                    )
                    break

    values = {name: _number(macros.get(name)) for name in MACROS}
    if any(value is None for value in values.values()):
        report.issues.append(
            QualityIssue("invalid_macros", "Calories and all three macros must be non-negative numbers.")
        )
    else:
        kcal = values["kcal"] or 0
        protein = values["protein_g"] or 0
        carbs = values["carbs_g"] or 0
        fat = values["fat_g"] or 0
        calculated = protein * 4 + carbs * 4 + fat * 9
        if not 80 <= kcal <= 1800 or protein > 180 or carbs > 300 or fat > 120:
            report.issues.append(
                QualityIssue("implausible_macros", "Meal nutrition falls outside plausible single-meal bounds.")
            )
        if kcal and abs(calculated - kcal) / kcal > 0.30:
            report.issues.append(
                QualityIssue("macro_energy_mismatch", "Calories are inconsistent with protein, carbohydrate, and fat.")
            )
        if evidence is not None:
            tolerances = {"kcal": 25.0, "protein_g": 2.0, "carbs_g": 2.0, "fat_g": 2.0}
            if any(abs(float(values[name] or 0.0) - evidence[name]) > tolerances[name] for name in MACROS):
                report.issues.append(
                    QualityIssue(
                        "ingredient_macro_mismatch",
                        "Meal nutrition does not equal the sum of its ingredient nutrition.",
                    )
                )
            ingredient_names = _words(" ".join(str(item.get("name") or "") for item in ingredients))
            if protein >= 25 and not any(_contains(ingredient_names, marker) for marker in PROTEIN_SOURCE_MARKERS):
                report.issues.append(
                    QualityIssue("missing_protein_source", "Claimed protein has no adequate ingredient source.")
                )
            if carbs >= 35 and not any(_contains(ingredient_names, marker) for marker in CARB_SOURCE_MARKERS):
                report.issues.append(
                    QualityIssue("missing_carb_source", "Claimed carbohydrate has no adequate ingredient source.")
                )
            if fat >= 15 and not any(_contains(ingredient_names, marker) for marker in FAT_SOURCE_MARKERS):
                report.issues.append(
                    QualityIssue("missing_fat_source", "Claimed fat has no adequate ingredient source.")
                )
        if target:
            for name in MACROS:
                target_value = _number(target.get(name))
                actual = values[name]
                if target_value and actual is not None and abs(actual - target_value) / target_value > 0.12:
                    report.issues.append(
                        QualityIssue(
                            "target_miss",
                            f"{name} is more than 12% from its target.",
                            severity=target_miss_severity,
                        )
                    )
                    break

    prep = _number(meal.get("prep_time_min"))
    cook = _number(meal.get("cook_time_min"))
    total = _number(meal.get("total_time_min"))
    if (
        prep is None
        or cook is None
        or total is None
        or not 1 <= prep <= 120
        or not 0 <= cook <= 180
        or not 1 <= total <= 240
    ):
        report.issues.append(QualityIssue("invalid_timing", "Preparation, cooking, and total times must be realistic."))
    elif total < prep + cook or total > prep + cook + 60:
        report.issues.append(
            QualityIssue("inconsistent_timing", "Total time conflicts with preparation and cooking time.")
        )
    wait_minutes = [
        float(value)
        for value in re.findall(
            r"(?:rest|chill|refrigerate|soak|marinate)[a-z ]{0,30}?(\d{1,3})\s*(?:minutes?|mins?)",
            instruction_words,
        )
    ]
    if "overnight" in instruction_words or (
        wait_minutes
        and total is not None
        and (max(wait_minutes) > total or total < float(cook or 0) + max(wait_minutes))
    ):
        report.issues.append(
            QualityIssue("inconsistent_wait_time", "Advertised total time omits required resting or chilling time.")
        )

    preparation_actions = ("boil", "cook", "simmer", "soak", "chill", "refrigerate", "overnight")
    instruction_steps = [_words(step) for step in instructions or []]
    has_unprepared_dry_grain = False
    for item in ingredients or []:
        if not isinstance(item, dict):
            continue
        identity = _words(f"{item.get('name', '')} {item.get('usda_search_query', '')}")
        if any(_contains(identity, state) for state in ("cooked", "granola", "instant", "prepared", "ready to eat")):
            continue
        grain = next(
            (
                marker
                for marker in ("oat", "oats", "rice", "quinoa", "pasta", "couscous", "barley", "bulgur")
                if _contains(identity, marker)
            ),
            None,
        )
        if grain and not any(
            _contains(step, grain) and any(action in step for action in preparation_actions)
            for step in instruction_steps
        ):
            has_unprepared_dry_grain = True
            break
    if has_unprepared_dry_grain:
        report.issues.append(
            QualityIssue("uncooked_dry_grain", "Dry grains must be cooked or fully soaked before serving.")
        )

    exclusion_hits = violates_exclusions(meal, exclusions or [])
    if exclusion_hits:
        report.issues.append(
            QualityIssue("excluded_ingredient", f"Meal contains an excluded ingredient: {', '.join(exclusion_hits)}.")
        )
    diet_hits = violates_diet(meal, diet)
    if diet_hits:
        report.issues.append(QualityIssue("diet_violation", f"Meal conflicts with the {diet} diet."))

    text = ingredient_text(meal)
    instruction_text = _words(" ".join(str(step) for step in instructions or []))
    hazards = sorted(marker for marker in NONFOOD_HAZARDS if _contains(text, marker))
    if hazards:
        report.issues.append(
            QualityIssue("unsafe_nonfood_ingredient", f"Recipe contains a non-food hazard: {', '.join(hazards)}.")
        )
    contains_animal_protein = any(_contains(text, marker) for marker in RAW_PROTEIN_MARKERS)
    contains_raw_animal_protein = _has_raw_animal_protein(meal)
    explicitly_raw = _contains(text, "raw") and contains_raw_animal_protein
    needs_doneness = contains_raw_animal_protein and bool(cook and cook > 0) or explicitly_raw
    if explicitly_raw and cook == 0:
        report.issues.append(QualityIssue("uncooked_raw_protein", "Raw animal protein cannot have zero cooking time."))
    if needs_doneness and not any(marker in instruction_text for marker in DONENESS_MARKERS):
        report.issues.append(QualityIssue("missing_doneness_cue", "Cooked animal protein needs a clear doneness cue."))
    internal_temps = re.findall(
        r"internal temperature[a-z ]{0,24}?(\d{2,3}(?:\.\d+)?)\s*°?\s*([fc])\b",
        instruction_text,
    )
    if internal_temps and contains_animal_protein:
        if any(_contains(text, marker) for marker in GROUND_MEAT_MARKERS):
            minimum_f = 160
        elif any(_contains(text, marker) for marker in ("chicken", "turkey")):
            minimum_f = 165
        elif any(_contains(text, marker) for marker in ("egg", "eggs")):
            minimum_f = 160
        else:
            minimum_f = 145
        fahrenheit_values = [
            float(value) * 9 / 5 + 32 if unit == "c" else float(value) for value, unit in internal_temps
        ]
        if any(fahrenheit < minimum_f for fahrenheit in fahrenheit_values):
            report.issues.append(
                QualityIssue(
                    "unsafe_internal_temperature",
                    f"Stated internal temperature is below the {minimum_f}°F safety minimum.",
                )
            )
    return report


EVALUATION_PROFILES = tuple(
    {
        "id": f"{diet}_{allergen}_free",
        "diet": diet,
        "exclusions": [allergen],
        "training": "mixed athlete training",
    }
    for diet in SUPPORTED_DIETS
    for allergen in SUPPORTED_ALLERGENS
) + (
    {
        "id": "vegan_top_nine_free",
        "diet": "vegan",
        "exclusions": list(SUPPORTED_ALLERGENS),
        "training": "strength and intervals",
    },
    {
        "id": "pescatarian_dairy_egg_wheat_free",
        "diet": "pescatarian",
        "exclusions": ["milk", "egg", "wheat"],
        "training": "long endurance",
    },
)


def evaluate_plan(meals: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    """Score generated fixtures or captured model output without storing athlete data."""
    reports = [
        validate_meal(
            meal,
            target=meal.get("target"),
            exclusions=list(profile.get("exclusions") or []),
            diet=str(profile.get("diet") or "omnivore"),
        )
        for meal in meals
    ]
    issue_counts: dict[str, int] = {}
    for report in reports:
        for code in report.codes():
            issue_counts[code] = issue_counts.get(code, 0) + 1
    passed = sum(report.safe for report in reports)
    return {
        "profile_id": profile.get("id"),
        "prompt_version": PROMPT_VERSION,
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "meals": len(meals),
        "passed": passed,
        "pass_rate": passed / len(meals) if meals else 0.0,
        "issue_counts": issue_counts,
    }
