from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PROMPT_VERSION = "meal-planner-2026-09-03-v1"
QUALITY_POLICY_VERSION = "nutrition-safety-2026-09-03-v1"

MACROS = ("kcal", "protein_g", "carbs_g", "fat_g")
ANIMAL_MEAT = {"beef", "chicken", "cod", "fish", "lamb", "pork", "salmon", "shrimp", "steak", "turkey", "tuna"}
ANIMAL_PRODUCTS = ANIMAL_MEAT | {"butter", "cheese", "egg", "eggs", "honey", "milk", "whey", "yogurt"}
MEAT = ANIMAL_MEAT - {"cod", "fish", "salmon", "shrimp", "tuna"}
ALLERGEN_ALIASES = {
    "milk": {"butter", "casein", "cheese", "cream", "feta", "ghee", "milk", "parmesan", "whey", "yogurt"},
    "dairy": {"butter", "casein", "cheese", "cream", "feta", "ghee", "milk", "parmesan", "whey", "yogurt"},
    "lactose_intolerant": {"butter", "cheese", "cream", "feta", "milk", "parmesan", "whey", "yogurt"},
    "milk_allergy": {"butter", "casein", "cheese", "cream", "feta", "ghee", "milk", "parmesan", "whey", "yogurt"},
    "dairy_allergy": {"butter", "casein", "cheese", "cream", "feta", "ghee", "milk", "parmesan", "whey", "yogurt"},
    "egg": {"egg", "eggs", "mayonnaise", "meringue"},
    "fish": {"anchovy", "cod", "fish", "salmon", "tilapia", "trout", "tuna"},
    "shellfish": {"crab", "lobster", "prawn", "shrimp"},
    "peanut": {"peanut", "peanuts"},
    "tree_nuts": {"almond", "cashew", "hazelnut", "pecan", "pistachio", "walnut"},
    "soy": {"edamame", "miso", "soy", "tempeh", "tofu"},
    "sesame": {"sesame", "tahini"},
    "wheat": {"bread", "couscous", "flour tortilla", "pasta", "seitan", "wheat"},
}
RAW_PROTEIN_MARKERS = ANIMAL_MEAT | {"egg", "eggs"}
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


def _words(value: Any) -> str:
    return re.sub(r"[^a-z0-9°]+", " ", str(value or "").lower()).strip()


def _contains(text: str, marker: str) -> bool:
    return bool(re.search(rf"\b{re.escape(marker)}\b", text))


def ingredient_text(meal: dict[str, Any]) -> str:
    return _words(f"{meal.get('title', '')} {json.dumps(meal.get('ingredients') or [], default=str)}")


def violates_exclusions(meal: dict[str, Any], exclusions: list[str]) -> list[str]:
    text = ingredient_text(meal)
    hits: list[str] = []
    for raw in exclusions:
        exclusion = _words(raw)
        aliases = ALLERGEN_ALIASES.get(exclusion.replace(" ", "_"), {exclusion})
        if any(_contains(text, alias) for alias in aliases if alias):
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
    return sorted(marker for marker in prohibited if _contains(text, marker))


def validate_meal(
    meal: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    exclusions: list[str] | None = None,
    diet: str | None = None,
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
    if not isinstance(instructions, list) or len([step for step in instructions if str(step).strip()]) < 2:
        report.issues.append(QualityIssue("incomplete_instructions", "At least two preparation steps are required."))

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
        if target:
            for name in MACROS:
                target_value = _number(target.get(name))
                actual = values[name]
                if target_value and actual is not None and abs(actual - target_value) / target_value > 0.25:
                    report.issues.append(QualityIssue("target_miss", f"{name} is more than 25% from its target."))
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
    elif total < max(prep, cook) or total > prep + cook + 60:
        report.issues.append(
            QualityIssue("inconsistent_timing", "Total time conflicts with preparation and cooking time.")
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
    contains_animal_protein = any(_contains(text, marker) for marker in RAW_PROTEIN_MARKERS)
    explicitly_raw = _contains(text, "raw") and contains_animal_protein
    needs_doneness = contains_animal_protein and bool(cook and cook > 0) or explicitly_raw
    if explicitly_raw and cook == 0:
        report.issues.append(QualityIssue("uncooked_raw_protein", "Raw animal protein cannot have zero cooking time."))
    if needs_doneness and not any(marker in instruction_text for marker in DONENESS_MARKERS):
        report.issues.append(QualityIssue("missing_doneness_cue", "Cooked animal protein needs a clear doneness cue."))
    return report


EVALUATION_PROFILES = (
    {"id": "endurance_omnivore", "diet": "omnivore", "exclusions": [], "training": "long endurance"},
    {"id": "hyrox_dairy_free", "diet": "omnivore", "exclusions": ["milk"], "training": "HYROX intervals"},
    {"id": "vegan_strength", "diet": "vegan", "exclusions": [], "training": "strength"},
    {"id": "vegetarian_nut_free", "diet": "vegetarian", "exclusions": ["peanut", "tree_nuts"], "training": "tempo"},
    {"id": "pescatarian_gluten_free", "diet": "pescatarian", "exclusions": ["wheat"], "training": "recovery"},
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
