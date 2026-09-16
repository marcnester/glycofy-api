from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.models import MealFeedback, MealPreferenceEvent, PlanMeal

_PROTEINS = {
    "chicken": ("chicken",),
    "turkey": ("turkey",),
    "beef": ("beef", "steak", "sirloin"),
    "pork": ("pork",),
    "salmon": ("salmon",),
    "tuna": ("tuna",),
    "white fish": ("cod", "tilapia", "halibut"),
    "shrimp": ("shrimp", "prawn"),
    "eggs": ("egg",),
    "tofu": ("tofu",),
    "tempeh": ("tempeh",),
    "legumes": ("lentil", "chickpea", "bean"),
    "Greek yogurt": ("greek yogurt",),
    "cottage cheese": ("cottage cheese",),
}
_STYLES = {
    "Mediterranean": ("mediterranean",),
    "Mexican-inspired": ("taco", "burrito", "fajita"),
    "Asian-inspired": ("stir-fry", "stir fry", "teriyaki", "sesame", "noodle"),
    "Italian-inspired": ("pasta", "marinara", "italian"),
    "bowls": ("bowl",),
    "wraps": ("wrap",),
    "salads": ("salad",),
    "soups": ("soup", "stew", "chili"),
}
_METHODS = {
    "baked": ("bake", "roast"),
    "grilled": ("grill",),
    "stovetop": ("skillet", "stovetop", "sauté", "saute"),
    "stir-fried": ("stir-fry", "stir fry"),
    "slow-cooked": ("slow cooker", "slow-cook"),
    "no-cook": ("no-cook", "no cook", "assemble", "overnight"),
}


def _matches(text: str, vocabulary: dict[str, tuple[str, ...]]) -> list[str]:
    return [label for label, terms in vocabulary.items() if any(term in text for term in terms)]


def meal_preference_features(meal: PlanMeal) -> dict[str, Any]:
    """Extract stable food patterns server-side; clients cannot invent learning attributes."""
    ingredients = [
        re.sub(r"\s+", " ", str(getattr(item, "name", "")).strip().lower())
        for item in (getattr(meal, "items", None) or [])
        if str(getattr(item, "name", "")).strip()
    ]
    text = " ".join([str(meal.title or ""), str(meal.instructions or ""), *ingredients]).lower()
    meta = meal.meta if isinstance(meal.meta, dict) else {}
    total_minutes = meta.get("total_minutes") or meta.get("total_time_minutes")
    try:
        total_minutes = int(total_minutes) if total_minutes is not None else None
    except (TypeError, ValueError):
        total_minutes = None
    return {
        "ingredients": ingredients[:20],
        "proteins": _matches(text, _PROTEINS),
        "styles": _matches(text, _STYLES),
        "methods": _matches(text, _METHODS),
        "total_minutes": total_minutes,
    }


def _ranked(scores: dict[str, float], *, positive: bool, limit: int = 8) -> list[str]:
    rows = ((key, value) for key, value in scores.items() if (value > 0 if positive else value < 0))
    return [key for key, _ in sorted(rows, key=lambda item: abs(item[1]), reverse=True)[:limit]]


def feedback_context(db: Session, user_id: int, limit: int = 60) -> dict[str, Any]:
    """Return a small, non-identifying preference summary for meal generation."""
    rows = (
        db.query(MealFeedback)
        .filter(MealFeedback.user_id == user_id)
        .order_by(MealFeedback.updated_at.desc())
        .limit(limit)
        .all()
    )
    events = (
        db.query(MealPreferenceEvent)
        .filter(MealPreferenceEvent.user_id == user_id)
        .order_by(MealPreferenceEvent.created_at.desc())
        .limit(limit * 2)
        .all()
    )
    if not rows and not events:
        return {"feedback_count": 0, "preference_signal_count": 0, "exploration_rate": 0.2}

    favorites = Counter(
        row.meal_title for row in rows if row.outcome == "eaten" and row.rating is not None and row.rating >= 4
    )
    avoid = Counter(
        row.meal_title for row in rows if row.outcome == "skipped" or (row.rating is not None and row.rating <= 2)
    )
    weights = {"love": 3.0, "repeat": 4.0, "avoid": -5.0, "swap": -2.0}
    feature_scores: dict[str, defaultdict[str, float]] = {
        key: defaultdict(float) for key in ("ingredients", "proteins", "styles", "methods")
    }
    title_scores: defaultdict[str, float] = defaultdict(float)
    explicit_by_meal: dict[int, str] = {}
    for event in reversed(events):
        weight = weights.get(event.signal, 0.0)
        title_scores[event.meal_title] += weight
        features = event.features if isinstance(event.features, dict) else {}
        for key in feature_scores:
            for value in features.get(key, []) or []:
                if value:
                    feature_scores[key][str(value)] += weight
        if event.source == "explicit" and event.plan_meal_id is not None:
            explicit_by_meal[event.plan_meal_id] = event.signal

    favorite_titles = Counter(favorites)
    avoided_titles = Counter(avoid)
    for title, score in title_scores.items():
        if score > 0:
            favorite_titles[title] += score
        elif score < 0:
            avoided_titles[title] += abs(score)

    return {
        "feedback_count": len(rows),
        "preference_signal_count": len(events),
        "favorite_meals": [title for title, _ in favorite_titles.most_common(6)],
        "avoid_repeating": [title for title, _ in avoided_titles.most_common(6)],
        "favored_ingredients": _ranked(feature_scores["ingredients"], positive=True),
        "avoided_ingredients": _ranked(feature_scores["ingredients"], positive=False),
        "favored_proteins": _ranked(feature_scores["proteins"], positive=True, limit=5),
        "avoided_proteins": _ranked(feature_scores["proteins"], positive=False, limit=5),
        "favored_meal_styles": _ranked(feature_scores["styles"], positive=True, limit=5),
        "avoided_meal_styles": _ranked(feature_scores["styles"], positive=False, limit=5),
        "favored_cooking_methods": _ranked(feature_scores["methods"], positive=True, limit=5),
        "avoided_cooking_methods": _ranked(feature_scores["methods"], positive=False, limit=5),
        "meal_reactions": {str(key): value for key, value in explicit_by_meal.items()},
        "exploration_rate": 0.2,
        "portion_signals": dict(Counter(row.portion for row in rows if row.portion)),
        "hunger_signals": dict(Counter(row.hunger_after for row in rows if row.hunger_after)),
        "energy_signals": dict(Counter(row.energy_after for row in rows if row.energy_after)),
        "digestion_signals": dict(Counter(row.digestion for row in rows if row.digestion)),
        "practicality_signals": dict(Counter(row.practicality for row in rows if row.practicality)),
    }
