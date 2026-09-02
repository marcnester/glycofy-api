# app/routers/llm_recommend.py — OpenAI-backed recommender with guardrails, caching, ratelimit, health + logging
# (daily + training-aware weekly recommender + training curve + weekly no-repeat + ai_idea payload)
# v2025-12-23a (+NEW: enforce no-repeat protein_item + carb_item within a day across main meals; snack flexible)

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import SessionLocal, get_db
from app.models import (
    Plan,
    PlanItem,
    PlanMeal,
    Recipe,
    User,
    UserPreference,  # ORM mapped to user_preferences
)
from app.services.training_nutrition import (
    MacroTargets,
    TrainingNutritionResult,
    calculate_training_nutrition,
)

# Optional OpenAI client (lazy import so dev works without the package)
ClientType = Any

router = APIRouter()
logger = logging.getLogger(__name__)

__all__ = ["router"]

# Canonical slots for a day – we expect these to be present in weekly templates.
SLOTS: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")
SLOT_ORDER: dict[str, int] = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}

# Enforce day-level (within-day) variety across these slots (snack is exempt by default).
_DAY_UNIQUE_SLOTS: set[str] = {"breakfast", "lunch", "dinner"}

# ===========================
# Pydantic models (Pydantic v2)
# ===========================


class MealTarget(BaseModel):
    slot: str = Field(..., pattern=r"^(breakfast|lunch|dinner|snack)$")
    kcal: float = Field(..., ge=0)
    protein_g: float = Field(..., ge=0)
    carbs_g: float = Field(..., ge=0)
    fat_g: float = Field(..., ge=0)


class RecommendRequest(BaseModel):
    date: str | None = None
    totals: dict[str, float] | None = None
    meals: list[MealTarget] = Field(default_factory=list)
    # external filters (e.g., ["gluten_free","low_fodmap"]); Diet from Profile is enforced separately
    diet_tags: list[str] | None = None


class RecipePick(BaseModel):
    id: int
    title: str
    meal_type: str
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    # keep this loose so JSON from DB passes through unchanged
    ingredients: Any | None = None
    instructions: str | None = None


class SlotRecommendation(BaseModel):
    """
    Per-slot LLM output.

    Shape is intentionally friendly to the UI:

      {
        "slot": "breakfast",
        "target": {...macros...},
        "recipe": { ... } | null,
        "deltas": { ... } | null,
        "reason": "text reason",
        "meta": {
          "provider": "openai|stub",
          "mode": "pick|create|empty",
          "ai_idea": { ... }  # when we generated a freeform/AI meal
        },
        "ai_idea": { ... }  # duplicated from meta.ai_idea for convenience
      }
    """

    slot: str
    target: dict[str, float]
    recipe: RecipePick | None = None
    deltas: dict[str, float] | None = None
    reason: str | None = None
    meta: dict[str, Any] | None = None  # lightweight audit + AI idea payload
    ai_idea: dict[str, Any] | None = None  # convenience mirror of meta.ai_idea


class RecommendResponse(BaseModel):
    provider: str
    items: list[SlotRecommendation]
    nutrition: dict[str, Any] | None = None


# ---------- Weekly models ----------


class WeeklyDayRequest(BaseModel):
    """
    Single day in a weekly request.

    `meals` are the MealTarget rows (usually cloned from a template day).
    `totals` can include overall macros; not required.
    `diet_tags` are per-day extras (e.g. "gluten_free"); Profile Diet is still enforced server-side.
    """

    date: str
    totals: dict[str, float] | None = None
    meals: list[MealTarget] = Field(default_factory=list, max_length=6)
    diet_tags: list[str] | None = Field(default=None, max_length=20)


class WeeklyRecommendRequest(BaseModel):
    days: list[WeeklyDayRequest] = Field(default_factory=list, max_length=14)


class WeeklyJobStartResponse(BaseModel):
    job_id: str
    status: str


class WeeklyJobStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    message: str
    completed_days: int = 0
    total_days: int = 7
    elapsed_seconds: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None


# ===========================
# Helpers (macro math + filtering)
# ===========================

_MACROS = ("kcal", "protein_g", "carbs_g", "fat_g")
_MEAL_TARGET_SPLITS = {"breakfast": 0.25, "lunch": 0.30, "dinner": 0.30, "snack": 0.15}


def _balanced_weekly_targets(day: WeeklyDayRequest) -> list[MealTarget]:
    """Keep daily goals stable without inheriting a malformed prior meal split."""
    existing = {meal.slot: meal for meal in day.meals}
    totals = day.totals or {}
    daily = {
        name: _safe_float(totals.get(name), sum(_safe_float(getattr(meal, name, 0.0)) for meal in day.meals))
        for name in _MACROS
    }
    if not all(value > 0 for value in daily.values()):
        return day.meals
    return [
        MealTarget(
            slot=slot,
            **{name: round(daily[name] * fraction, 1) for name in _MACROS},
        )
        for slot, fraction in _MEAL_TARGET_SPLITS.items()
        if slot in existing
    ]


_SLOT_RECOMMENDATION_ATTEMPTS = 3


def _normalize_slot(s: str) -> str:
    return (s or "").strip().lower()


def _baseline_from_meals(meals: list[MealTarget]) -> MacroTargets:
    return MacroTargets(
        kcal=sum(max(0.0, meal.kcal) for meal in meals),
        protein_g=sum(max(0.0, meal.protein_g) for meal in meals),
        carbs_g=sum(max(0.0, meal.carbs_g) for meal in meals),
        fat_g=sum(max(0.0, meal.fat_g) for meal in meals),
    )


def _apply_nutrition_targets(
    meals: list[MealTarget],
    nutrition: TrainingNutritionResult,
) -> list[MealTarget]:
    baseline = nutrition.baseline
    final = nutrition.final
    scales = {
        "kcal": final.kcal / baseline.kcal if baseline.kcal > 0 else 1.0,
        "protein_g": final.protein_g / baseline.protein_g if baseline.protein_g > 0 else 1.0,
        "carbs_g": final.carbs_g / baseline.carbs_g if baseline.carbs_g > 0 else 1.0,
        "fat_g": final.fat_g / baseline.fat_g if baseline.fat_g > 0 else 1.0,
    }
    return [
        MealTarget(
            slot=meal.slot,
            kcal=max(0.0, meal.kcal * scales["kcal"]),
            protein_g=max(0.0, meal.protein_g * scales["protein_g"]),
            carbs_g=max(0.0, meal.carbs_g * scales["carbs_g"]),
            fat_g=max(0.0, meal.fat_g * scales["fat_g"]),
        )
        for meal in meals
    ]


def _score_recipe_vs_target(rec: Recipe, tgt: MealTarget) -> tuple[float, dict[str, float]]:
    deltas: dict[str, float] = {}
    score = 0.0
    for k in _MACROS:
        rv = getattr(rec, k, None)
        tv = getattr(tgt, k, 0.0)
        if rv is None:
            deltas[k] = float("nan")
            continue
        delta = abs(float(rv) - float(tv))
        deltas[k] = delta
        score += delta
    return score, deltas


def _coerce_tag_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return [str(x).strip().lower() for x in val if str(x).strip()]
    if isinstance(val, dict):
        out: list[str] = []
        for _, v in val.items():
            if isinstance(v, (list, tuple, set)):
                out.extend([str(x).strip().lower() for x in v if str(x).strip()])
            else:
                s = str(v).strip().lower()
                if s:
                    out.append(s)
        seen: dict[str, None] = {}
        for t in out:
            if t not in seen:
                seen[t] = None
        return list(seen.keys())
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, (list, tuple, set)):
                return [str(x).strip().lower() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [p.strip().lower() for p in s.split(",") if p.strip()]
    s = str(val).strip().lower()
    return [s] if s else []


# ===========================
# Protein group + protein item inference
# ===========================


def _guess_protein_group_from_text(text: str) -> str:
    t = (text or "").lower()

    fish_words = [
        "salmon",
        "cod",
        "trout",
        "tuna",
        "sardine",
        "mackerel",
        "anchovy",
        "halibut",
        "tilapia",
        "shrimp",
        "prawn",
        "scallop",
        "crab",
        "lobster",
        "seafood",
        "fish",
        "mussel",
        "clam",
        "oyster",
    ]
    poultry_words = ["chicken", "turkey"]
    beef_words = ["beef", "steak", "ground beef"]
    pork_words = ["pork", "ham", "bacon", "sausage", "chorizo"]
    plant_words = [
        "tofu",
        "tempeh",
        "lentil",
        "chickpea",
        "black bean",
        "kidney bean",
        "edamame",
        "beans",
        "hummus",
        "seitan",
    ]
    egg_words = ["egg ", " eggs", "omelet", "omelette", "scramble", "frittata"]
    dairy_words = ["yogurt", "cottage cheese", "greek yogurt", "ricotta", "paneer", "cheese"]

    if any(w in t for w in fish_words):
        return "fish"
    if any(w in t for w in poultry_words):
        return "poultry"
    if any(w in t for w in beef_words):
        return "beef"
    if any(w in t for w in pork_words):
        return "pork"
    if any(w in t for w in plant_words):
        return "plant"
    if any(w in t for w in egg_words):
        return "eggs"
    if any(w in t for w in dairy_words):
        return "dairy"
    return "unknown"


def _guess_protein_item_from_text(text: str) -> str:
    """
    A more specific 'protein key' that we enforce day-level uniqueness on.

    Example: allows fish variety within a day:
      salmon + tuna + shrimp OK
    but avoids repeating the same one:
      salmon + salmon NOT OK

    We keep this best-effort + conservative.
    """
    t = (text or "").lower()

    # Fish / seafood specifics
    fish_items = [
        "salmon",
        "tuna",
        "shrimp",
        "prawn",
        "cod",
        "trout",
        "sardine",
        "mackerel",
        "anchovy",
        "halibut",
        "tilapia",
        "scallop",
        "crab",
        "lobster",
        "mussel",
        "clam",
        "oyster",
    ]
    for it in fish_items:
        if it in t:
            return it

    # Common meats
    if "chicken" in t:
        return "chicken"
    if "turkey" in t:
        return "turkey"
    if "beef" in t or "steak" in t:
        return "beef"
    if "pork" in t:
        return "pork"
    if "ham" in t:
        return "ham"
    if "bacon" in t:
        return "bacon"
    if "sausage" in t or "chorizo" in t:
        return "sausage"

    # Plant proteins (be slightly specific)
    plant_items = [
        "tofu",
        "tempeh",
        "lentil",
        "chickpea",
        "black bean",
        "kidney bean",
        "edamame",
        "seitan",
        "hummus",
    ]
    for it in plant_items:
        if it in t:
            return it

    # Eggs / dairy (can be specific-ish)
    if "egg" in t or "omelet" in t or "frittata" in t or "scramble" in t:
        return "eggs"
    dairy_items = ["greek yogurt", "yogurt", "cottage cheese", "ricotta", "paneer", "cheese"]
    for it in dairy_items:
        if it in t:
            return it.replace(" ", "_")

    # If we know it's a group but not a specific item, return the group as a fallback key
    # (This will be stricter than desired, but only when we can't detect the item.)
    pg = _guess_protein_group_from_text(t)
    return pg if pg and pg != "unknown" else "unknown"


def _guess_protein_group_for_recipe(r: Recipe) -> str:
    # Prefer explicit column if present; else guess
    pg = getattr(r, "protein_group", None)
    if isinstance(pg, str) and pg.strip():
        return pg.strip().lower()
    title = getattr(r, "title", "") or ""
    ingredients = getattr(r, "ingredients", "") or ""
    return _guess_protein_group_from_text(f"{title} {ingredients}")


def _guess_protein_item_for_recipe(r: Recipe) -> str:
    # If you later add an explicit column, prefer it here.
    title = getattr(r, "title", "") or ""
    ingredients = getattr(r, "ingredients", "") or ""
    return _guess_protein_item_from_text(f"{title} {ingredients}")


# ===========================
# Carb item inference (day-level uniqueness)
# ===========================


def _guess_carb_item_from_text(text: str) -> str:
    """
    Carb key we enforce uniqueness on across breakfast/lunch/dinner.
    Examples: oats, rice, pasta, quinoa, potato, bread, tortillas, etc.
    """
    t = (text or "").lower()

    # Prefer more specific terms first
    carb_items = [
        "oats",
        "oatmeal",
        "granola",
        "rice",
        "quinoa",
        "pasta",
        "noodles",
        "ramen",
        "udon",
        "soba",
        "couscous",
        "bulgur",
        "barley",
        "farro",
        "polenta",
        "corn",
        "tortilla",
        "wrap",
        "bread",
        "bagel",
        "toast",
        "pita",
        "naan",
        "potato",
        "sweet potato",
        "yams",
        "plantain",
        "beans",  # can be carb-ish for some meals; still useful for variety
        "lentil",
        "chickpea",
    ]
    # Handle multi-word first
    if "sweet potato" in t:
        return "sweet_potato"

    # Map common variants
    if "oatmeal" in t:
        return "oats"
    if "noodle" in t or "ramen" in t or "udon" in t or "soba" in t:
        return "pasta"
    if "tortilla" in t or "wrap" in t:
        return "tortilla"
    if "bagel" in t:
        return "bagel"
    if "bread" in t or "toast" in t or "pita" in t or "naan" in t:
        return "bread"

    for it in carb_items:
        if it in t:
            return it.replace(" ", "_")

    return "unknown"


def _guess_carb_item_for_recipe(r: Recipe) -> str:
    title = getattr(r, "title", "") or ""
    ingredients = getattr(r, "ingredients", "") or ""
    return _guess_carb_item_from_text(f"{title} {ingredients}")


# ===========================
# Diet-tag filtering
# ===========================


def _filter_by_diet_tags(
    items: list[Recipe],
    diet_tags: list[str] | None,
    primary_diet: str,
) -> list[Recipe]:
    if not diet_tags:
        logger.info(
            "LLM diet filter: primary_diet=%r has no explicit diet_tags; skipping filter",
            primary_diet,
        )
        return items

    need = {t.lower().strip() for t in diet_tags if t and t.strip()}
    if not need:
        logger.info(
            "LLM diet filter: primary_diet=%r diet_tags normalized empty; skipping filter",
            primary_diet,
        )
        return items

    out: list[Recipe] = []
    for r in items:
        raw_tags = getattr(r, "diet_tags", None)
        have = set(_coerce_tag_list(raw_tags))
        if need.issubset(have):
            out.append(r)

    logger.info(
        "LLM diet filter: primary_diet=%r requested_tags=%s -> kept=%d / total=%d",
        primary_diet,
        sorted(list(need)),
        len(out),
        len(items),
    )

    if not out and primary_diet in ("", "omnivore"):
        logger.warning(
            "LLM diet filter: primary_diet=%r requested_tags=%s eliminated all %d candidates; "
            "falling back to unfiltered list",
            primary_diet,
            sorted(list(need)),
            len(items),
        )
        return items

    if not out and primary_diet in ("pescatarian", "vegetarian", "vegan"):
        logger.warning(
            "LLM diet filter: STRICT primary_diet=%r requested_tags=%s removed all %d candidates; "
            "NOT falling back to omnivore",
            primary_diet,
            sorted(list(need)),
            len(items),
        )

    return out


def _top_k_candidates(
    db: Session,
    slot: str,
    tgt: MealTarget,
    diet_tags: list[str] | None,
    primary_diet: str,
    k: int = 6,
    exclude_ids: set[int] | None = None,
    disallowed_protein_groups: set[str] | None = None,  # weekly cap
    disallowed_protein_items: set[str] | None = None,  # day-level variety
    disallowed_carb_items: set[str] | None = None,  # day-level variety
    disallowed_meal_keys: set[str] | None = None,  # semantic weekly uniqueness
    ingredient_exclusions: list[str] | None = None,
) -> list[tuple[Recipe, dict[str, float], float]]:
    q = db.query(Recipe).filter(Recipe.meal_type == slot)
    all_items: list[Recipe] = q.all()
    logger.info(
        "LLM top_k_candidates: slot=%s raw_count=%d merged_diet_tags=%s primary_diet=%r",
        slot,
        len(all_items),
        diet_tags or [],
        primary_diet,
    )

    items = _filter_by_diet_tags(all_items, diet_tags, primary_diet)

    if ingredient_exclusions:
        before = len(items)
        items = [r for r in items if not _recipe_violates_exclusions(r, ingredient_exclusions)]
        logger.info(
            "LLM top_k_candidates: slot=%s ingredient_exclusions=%s removed=%d remaining=%d",
            slot,
            ingredient_exclusions,
            before - len(items),
            len(items),
        )

    exclude_set = set(exclude_ids) if exclude_ids else None
    if exclude_set:
        before = len(items)
        items = [r for r in items if int(getattr(r, "id", -1)) not in exclude_set]
        logger.info(
            "LLM top_k_candidates: slot=%s excluded_used_ids=%d remaining=%d",
            slot,
            before - len(items),
            len(items),
        )

    banned_meals = {key for key in (disallowed_meal_keys or set()) if key}
    if banned_meals:
        before = len(items)
        items = [r for r in items if _meal_similarity_key(getattr(r, "title", None)) not in banned_meals]
        logger.info(
            "LLM top_k_candidates: slot=%s excluded_used_meal_keys=%d remaining=%d",
            slot,
            before - len(items),
            len(items),
        )

    banned_pg = {pg.strip().lower() for pg in (disallowed_protein_groups or set()) if pg.strip()}
    if banned_pg:
        before = len(items)
        filtered: list[Recipe] = []
        for r in items:
            pg = _guess_protein_group_for_recipe(r)
            pg_norm = (pg or "").strip().lower()
            if pg_norm and pg_norm in banned_pg:
                continue
            filtered.append(r)
        items = filtered
        logger.info(
            "LLM top_k_candidates: slot=%s disallowed_protein_groups=%s removed=%d remaining=%d",
            slot,
            sorted(list(banned_pg)),
            before - len(items),
            len(items),
        )

    banned_pi = {x.strip().lower() for x in (disallowed_protein_items or set()) if x and x.strip()}
    if banned_pi:
        before = len(items)
        filtered2: list[Recipe] = []
        for r in items:
            pi = _guess_protein_item_for_recipe(r)
            pi_norm = (pi or "").strip().lower()
            if pi_norm and pi_norm in banned_pi:
                continue
            filtered2.append(r)
        items = filtered2
        logger.info(
            "LLM top_k_candidates: slot=%s disallowed_protein_items=%s removed=%d remaining=%d",
            slot,
            sorted(list(banned_pi)),
            before - len(items),
            len(items),
        )

    banned_ci = {x.strip().lower() for x in (disallowed_carb_items or set()) if x and x.strip()}
    if banned_ci:
        before = len(items)
        filtered3: list[Recipe] = []
        for r in items:
            ci = _guess_carb_item_for_recipe(r)
            ci_norm = (ci or "").strip().lower()
            if ci_norm and ci_norm in banned_ci:
                continue
            filtered3.append(r)
        items = filtered3
        logger.info(
            "LLM top_k_candidates: slot=%s disallowed_carb_items=%s removed=%d remaining=%d",
            slot,
            sorted(list(banned_ci)),
            before - len(items),
            len(items),
        )

    if not items:
        logger.warning(
            "LLM top_k_candidates: slot=%s diet_tags=%s primary_diet=%r produced 0 candidates (after excludes/variety caps)",
            slot,
            diet_tags or [],
            primary_diet,
        )

    # Rank only recipes that can actually be cooked. Filtering after slicing
    # the top K can otherwise hide valid measured recipes ranked just below a
    # cluster of incomplete legacy rows and incorrectly force an AI call.
    before_quantities = len(items)
    items = [recipe for recipe in items if _recipe_has_quantified_ingredients(recipe)]
    if before_quantities != len(items):
        logger.info(
            "LLM top_k_candidates: slot=%s removed_unquantified=%d remaining=%d",
            slot,
            before_quantities - len(items),
            len(items),
        )

    scored: list[tuple[Recipe, dict[str, float], float]] = []
    for r in items:
        score, deltas = _score_recipe_vs_target(r, tgt)
        scored.append((r, deltas, score))

    scored.sort(key=lambda t: t[2])

    summary = [
        {
            "id": r.id,
            "title": r.title,
            "diet_tags": _coerce_tag_list(getattr(r, "diet_tags", None)),
            "protein_group": _guess_protein_group_for_recipe(r),
            "protein_item": _guess_protein_item_for_recipe(r),
            "carb_item": _guess_carb_item_for_recipe(r),
            "score": s,
        }
        for (r, _d, s) in scored[:k]
    ]
    logger.info(
        "LLM top_k_candidates: slot=%s returning_top_k=%d summary=%s",
        slot,
        min(k, len(scored)),
        summary,
    )

    return scored[:k]


def _recipe_pick_from_model(r: Recipe) -> RecipePick:
    return RecipePick(
        id=r.id,
        title=r.title,
        meal_type=r.meal_type,
        kcal=getattr(r, "kcal", None),
        protein_g=getattr(r, "protein_g", None),
        carbs_g=getattr(r, "carbs_g", None),
        fat_g=getattr(r, "fat_g", None),
        ingredients=getattr(r, "ingredients", None),
        instructions=getattr(r, "instructions", None),
    )


# ===========================
# Preferences → diet tags
# ===========================


def _diet_tags_from_preferences(pref: UserPreference | None) -> list[str]:
    if not pref:
        logger.info("LLM prefs: no UserPreference row, treating as omnivore (no diet tags)")
        return []

    raw_type = getattr(pref, "diet_type", None) or getattr(pref, "diet", None) or ""
    diet_type = str(raw_type).strip().lower()

    logger.info(
        "LLM prefs: user_id=%s raw_diet_type=%r",
        getattr(pref, "user_id", None),
        raw_type,
    )

    if not diet_type or diet_type == "omnivore":
        logger.info("LLM prefs: diet_type=%r -> no diet tags (omnivore)", diet_type)
        return []

    if diet_type == "pescatarian":
        logger.info("LLM prefs: diet_type=pescatarian -> ['pescatarian']")
        return ["pescatarian"]
    if diet_type == "vegetarian":
        logger.info("LLM prefs: diet_type=vegetarian -> ['vegetarian']")
        return ["vegetarian"]
    if diet_type == "vegan":
        logger.info("LLM prefs: diet_type=vegan -> ['vegan']")
        return ["vegan"]

    logger.warning(
        "LLM prefs: unknown diet_type=%r -> treating as omnivore (no tags)",
        diet_type,
    )
    return []


def _primary_diet_from_preferences(pref: UserPreference | None) -> str:
    if not pref:
        return "omnivore"
    raw_type = getattr(pref, "diet_type", None) or getattr(pref, "diet", None) or ""
    diet_type = str(raw_type).strip().lower()
    if not diet_type:
        return "omnivore"
    return diet_type


def _get_user_pref(db: Session, user_id: int) -> UserPreference | None:
    try:
        pref = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
        if pref:
            logger.info(
                "LLM prefs: loaded UserPreference for user_id=%s diet_type=%r ingredient_exclusions=%r",
                user_id,
                getattr(pref, "diet_type", None),
                getattr(pref, "ingredient_exclusions", None),
            )
        else:
            logger.info("LLM prefs: no UserPreference row for user_id=%s", user_id)
        return pref
    except Exception as e:
        logger.exception("LLM prefs: error loading UserPreference for user_id=%s: %s", user_id, e)
        return None


def _preference_exclusions(pref: UserPreference | None) -> list[str]:
    if not pref:
        return []
    raw = getattr(pref, "ingredient_exclusions", None)
    out: list[str] = []
    if isinstance(raw, str):
        out.extend(part.strip().lower() for part in raw.split(",") if part.strip())
    elif isinstance(raw, list):
        out.extend(str(part).strip().lower() for part in raw if str(part).strip())
    allergies = getattr(pref, "allergies", None) or []
    if isinstance(allergies, (list, tuple, set)):
        out.extend(str(item).strip().lower() for item in allergies if str(item).strip())
    return list(dict.fromkeys(out))


_DAIRY_MARKERS = {
    "butter",
    "buttermilk",
    "casein",
    "cheddar",
    "cheese",
    "cottage cheese",
    "cream",
    "creme fraiche",
    "feta",
    "ghee",
    "kefir",
    "mascarpone",
    "mozzarella",
    "parmesan",
    "ricotta",
    "whey",
    "yogurt",
    "yoghurt",
}
_PLANT_MILK_MARKERS = {
    "almond milk",
    "cashew milk",
    "coconut milk",
    "hemp milk",
    "oat milk",
    "plant milk",
    "rice milk",
    "soy milk",
    "non-dairy milk",
    "lactose-free milk",
}
_ALLERGEN_MARKERS = {
    "egg": {"egg", "eggs", "mayonnaise", "meringue"},
    "fish": {"fish", "salmon", "tuna", "cod", "tilapia", "trout", "anchovy", "sardine"},
    "shellfish": {"shellfish", "shrimp", "prawn", "crab", "lobster", "crayfish"},
    "tree_nuts": {
        "tree nut",
        "almond",
        "cashew",
        "walnut",
        "pecan",
        "pistachio",
        "hazelnut",
        "macadamia",
        "brazil nut",
    },
    "peanut": {"peanut", "groundnut"},
    "wheat": {"wheat", "flour", "bread", "pasta", "couscous", "seitan", "bulgur", "farro"},
    "soy": {"soy", "soya", "tofu", "tempeh", "edamame", "miso"},
    "sesame": {"sesame", "tahini"},
}


def _text_violates_exclusions(text: str, exclusions: list[str]) -> bool:
    haystack = re.sub(r"\s+", " ", str(text or "").lower())
    for exclusion in exclusions:
        term = re.sub(r"\s+", " ", exclusion.strip().lower())
        if not term:
            continue
        if "lactose" in term or term in {"dairy", "milk allergy", "dairy allergy"}:
            if any(marker in haystack for marker in _DAIRY_MARKERS):
                return True
            if "milk" in haystack and not any(marker in haystack for marker in _PLANT_MILK_MARKERS):
                return True
            continue
        canonical = term.replace(" ", "_")
        if canonical == "milk":
            if any(marker in haystack for marker in _DAIRY_MARKERS):
                return True
            if "milk" in haystack and not any(marker in haystack for marker in _PLANT_MILK_MARKERS):
                return True
            continue
        markers = _ALLERGEN_MARKERS.get(canonical)
        if markers and any(re.search(rf"\b{re.escape(marker)}s?\b", haystack) for marker in markers):
            return True
        # The textbox is ingredient-oriented, so literal matching remains the
        # safest behavior for user-entered foods such as mushrooms or cilantro.
        if term in haystack:
            return True
    return False


def _recipe_violates_exclusions(recipe: Recipe, exclusions: list[str]) -> bool:
    text = f"{getattr(recipe, 'title', '')} {json.dumps(getattr(recipe, 'ingredients', None) or [], default=str)}"
    return _text_violates_exclusions(text, exclusions)


# ===========================
# OpenAI client + guardrails
# ===========================


def _get_openai_client() -> ClientType | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("LLM: OPENAI_API_KEY is not set; falling back to heuristic mode")
        return None
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        logger.exception("LLM: failed to import OpenAI client: %s", e)
        return None
    return OpenAI(api_key=api_key)


def _openai_model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini-2024-07-18")


class _Budget:
    def __init__(self) -> None:
        self.day = self._today_key()
        self.spent_usd = 0.0
        self.failures = 0
        self.last_failure_ts = 0.0

    @staticmethod
    def _today_key() -> str:
        now = datetime.now(UTC)
        return now.strftime("%Y-%m-%d")

    def reset_if_new_day(self) -> None:
        key = self._today_key()
        if key != self.day:
            self.day = key
            self.spent_usd = 0.0
            self.failures = 0
            self.last_failure_ts = 0.0


_BUDGET = _Budget()


def _daily_budget_usd() -> float:
    try:
        return float(os.environ.get("OPENAI_DAILY_BUDGET_USD", "1.00"))
    except Exception:
        return 1.00


def _allow_new_recipe() -> bool:
    val = os.environ.get("LLM_ALLOW_NEW_RECIPE", "true").strip().lower()
    return val in ("1", "true", "yes", "on")


def _circuit_open() -> bool:
    window = 60.0
    if _BUDGET.failures < 3:
        return False
    return (time.time() - _BUDGET.last_failure_ts) < window


def _record_success(cost_usd: float) -> None:
    _BUDGET.reset_if_new_day()
    _BUDGET.spent_usd += max(0.0, cost_usd)
    _BUDGET.failures = 0


def _record_failure() -> None:
    _BUDGET.reset_if_new_day()
    _BUDGET.failures += 1
    _BUDGET.last_failure_ts = time.time()


def _estimate_cost_from_usage(usage: dict[str, Any] | None) -> float:
    if not usage:
        return 0.0
    try:
        input_tokens = float(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = float(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        p_in = float(os.environ.get("OPENAI_PRICE_PER_1K_INPUT", "0.0005"))
        p_out = float(os.environ.get("OPENAI_PRICE_PER_1K_OUTPUT", "0.0015"))
        return (input_tokens / 1000.0) * p_in + (output_tokens / 1000.0) * p_out
    except Exception:
        return 0.0


def _sleep_backoff(attempt: int) -> None:
    base = 0.3 * (2**attempt)
    jitter = 0.05 + (0.1 * math.sin(time.time()))
    time.sleep(base + jitter)


def _extract_usage_meta(resp: Any) -> tuple[dict[str, Any] | None, float]:
    usage_obj = getattr(resp, "usage", None)
    usage: dict[str, Any] | None = None
    if usage_obj is not None:
        try:
            if isinstance(usage_obj, dict):
                usage = usage_obj
            else:
                usage = usage_obj.model_dump()  # type: ignore[attr-defined]
        except Exception:
            try:
                usage = dict(usage_obj)  # type: ignore[arg-type]
            except Exception:
                usage = None
    cost_est = _estimate_cost_from_usage(usage)
    return usage, cost_est


def _safe_openai_json_pick(
    client: ClientType,
    model: str,
    system: str,
    user_payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    meta: dict[str, Any] = {"provider": "openai", "model": model, "mode": "pick_or_create"}
    if client is None:
        meta.update({"fallback": "no_client"})
        logger.info("LLM: no client available; using heuristic mode")
        return None, meta

    _BUDGET.reset_if_new_day()

    if _circuit_open():
        meta.update({"fallback": "circuit_open"})
        logger.warning("LLM: circuit is open; skipping OpenAI call")
        return None, meta

    if _BUDGET.spent_usd >= _daily_budget_usd():
        meta.update({"fallback": "budget_exhausted"})
        logger.warning("LLM: daily budget exhausted; skipping OpenAI call")
        return None, meta

    err: str | None = None
    t0 = time.time()
    for attempt in range(3):
        try:
            logger.info(
                "LLM: calling OpenAI (chat.completions) model=%s attempt=%d payload_summary=%s",
                model,
                attempt,
                {
                    "slot": user_payload.get("slot"),
                    "target_macros": user_payload.get("target_macros"),
                    "diet_tags": user_payload.get("diet_tags"),
                    "candidates_ids": [c.get("id") for c in user_payload.get("candidates", [])],
                    "used_protein_items_today": user_payload.get("used_protein_items_today", []),
                    "used_carb_items_today": user_payload.get("used_carb_items_today", []),
                    "used_recipe_ids_week": user_payload.get("used_recipe_ids_week")
                    or user_payload.get("used_recipe_ids"),
                    "banned_protein_groups_slot_week": user_payload.get("banned_protein_groups_slot_week", []),
                    "disallowed_protein_items_today": user_payload.get("disallowed_protein_items_today", []),
                    "disallowed_carb_items_today": user_payload.get("disallowed_carb_items_today", []),
                },
            )
            resp = client.chat.completions.create(
                model=model,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            )
            latency_ms = int((time.time() - t0) * 1000)
            meta["latency_ms"] = latency_ms

            usage, cost_est = _extract_usage_meta(resp)
            if usage:
                meta["usage"] = usage
            meta["cost_usd"] = round(cost_est, 6)
            _record_success(cost_est)

            content = None
            try:
                if resp.choices and resp.choices[0].message and resp.choices[0].message.content:
                    content = resp.choices[0].message.content
            except Exception:
                content = None

            try:
                data = json.loads(content) if content else None
            except Exception:
                data = None

            logger.info(
                "LLM response parsed=%s slot=%s date=%s",
                isinstance(data, dict),
                user_payload.get("slot"),
                user_payload.get("date"),
            )

            if not data or not isinstance(data, dict):
                meta["fallback"] = "parse_error"
                logger.warning("LLM: invalid JSON response; falling back to heuristic")
                return None, meta

            return data, meta

        except Exception as e:
            err = str(e)
            _record_failure()
            logger.exception("LLM: error calling OpenAI (attempt %d): %s", attempt, e)
            if attempt < 2:
                _sleep_backoff(attempt)
            else:
                break

    meta.update({"fallback": "llm_error", "error": err})
    return None, meta


# ===========================
# Training load helpers (for weekly macros + visual curve)
# ===========================


# (UNCHANGED – your existing training helpers)
def _get_activity_model():
    try:
        from app import models as app_models  # type: ignore
    except Exception:
        return None
    return getattr(app_models, "Activity", None)


def _compute_training_load_for_date(db: Session, user_id: int, date_str: str) -> dict[str, Any]:
    Activity = _get_activity_model()
    if Activity is None:
        logger.info("Training load: Activity model not available; treating %s as rest", date_str)
        return {
            "score": 0.0,
            "tss": 0.0,
            "work_kj": 0.0,
            "kcal": 0.0,
            "duration_min": 0.0,
            "distance_km": 0.0,
            "is_race": False,
            "metric_name": "score",
            "metric_value": 0.0,
        }

    try:
        day = datetime.fromisoformat(date_str).date()
    except Exception:
        logger.warning("Training load: invalid date %r; treating as rest", date_str)
        return {
            "score": 0.0,
            "tss": 0.0,
            "work_kj": 0.0,
            "kcal": 0.0,
            "duration_min": 0.0,
            "distance_km": 0.0,
            "is_race": False,
            "metric_name": "score",
            "metric_value": 0.0,
        }

    date_col = getattr(Activity, "start_date_local", None) or getattr(Activity, "start_date", None)
    if date_col is None:
        date_col = getattr(Activity, "start_time", None)

    if date_col is None:
        logger.warning("Training load: Activity model has no suitable date column; treating %s as rest", date_str)
        return {
            "score": 0.0,
            "tss": 0.0,
            "work_kj": 0.0,
            "kcal": 0.0,
            "duration_min": 0.0,
            "distance_km": 0.0,
            "is_race": False,
            "metric_name": "score",
            "metric_value": 0.0,
        }

    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    try:
        q = (
            db.query(Activity)
            .filter(Activity.user_id == user_id)  # type: ignore[attr-defined]
            .filter(date_col >= day_start)
            .filter(date_col < day_end)
        )
        acts = q.all()
    except Exception as e:
        logger.exception("Training load: query failed for user_id=%s date=%s: %s", user_id, date_str, e)
        return {
            "score": 0.0,
            "tss": 0.0,
            "work_kj": 0.0,
            "kcal": 0.0,
            "duration_min": 0.0,
            "distance_km": 0.0,
            "is_race": False,
            "metric_name": "score",
            "metric_value": 0.0,
        }

    if not acts:
        return {
            "score": 0.0,
            "tss": 0.0,
            "work_kj": 0.0,
            "kcal": 0.0,
            "duration_min": 0.0,
            "distance_km": 0.0,
            "is_race": False,
            "metric_name": "score",
            "metric_value": 0.0,
        }

    total_tss = 0.0
    total_work_kj = 0.0
    total_kcal = 0.0
    duration_min = 0.0
    distance_km = 0.0
    is_race = False

    for a in acts:
        try:
            atype = getattr(a, "activity_type", None) or getattr(a, "sport", None)
            if isinstance(atype, str) and atype.lower() in {"race", "triathlon", "ironman"}:
                is_race = True
        except Exception:
            pass
        try:
            if getattr(a, "is_race", False):
                is_race = True
        except Exception:
            pass

        for name in ("tss", "training_load", "stress_score"):
            try:
                val = getattr(a, name, None)
            except Exception:
                val = None
            if val is not None:
                try:
                    v = float(val)
                except Exception:
                    v = 0.0
                total_tss += max(0.0, v)
                break

        try:
            work = getattr(a, "work_kj", None)
            if work is not None:
                total_work_kj += max(0.0, float(work))
        except Exception:
            pass

        try:
            kcal = getattr(a, "kcal", None)
            if kcal is not None:
                total_kcal += max(0.0, float(kcal))
        except Exception:
            pass

        dur = None
        dist = None
        try:
            dur = getattr(a, "duration_s", None)
        except Exception:
            dur = None
        if dur is None:
            try:
                dur = getattr(a, "moving_time_s", None)
            except Exception:
                dur = None
        if dur is None:
            try:
                dur = getattr(a, "elapsed_time", None)
            except Exception:
                dur = None

        try:
            dist = getattr(a, "distance_m", None)
        except Exception:
            dist = None
        if dist is None:
            try:
                dist = getattr(a, "distance", None)
            except Exception:
                dist = None

        try:
            if dur:
                duration_min += max(0.0, float(dur) / 60.0)
        except Exception:
            pass
        try:
            if dist:
                distance_km += max(0.0, float(dist) / 1000.0)
        except Exception:
            pass

    score = 0.0
    metric_name = "score"
    metric_value = 0.0

    if total_tss > 0:
        score = total_tss
        metric_name = "tss"
        metric_value = total_tss
    elif total_work_kj > 0:
        score = total_work_kj
        metric_name = "work_kj"
        metric_value = total_work_kj
    elif total_kcal > 0:
        score = total_kcal
        metric_name = "kcal"
        metric_value = total_kcal
    else:
        score = duration_min + 3.0 * distance_km
        metric_name = "score"
        metric_value = score

    logger.info(
        "Training load: user_id=%s date=%s acts=%d metric=%s value=%.2f tss=%.2f work_kj=%.1f kcal=%.1f "
        "dur_min=%.1f dist_km=%.1f is_race=%s",
        user_id,
        date_str,
        len(acts),
        metric_name,
        metric_value,
        total_tss,
        total_work_kj,
        total_kcal,
        duration_min,
        distance_km,
        is_race,
    )

    return {
        "score": float(score),
        "tss": float(total_tss),
        "work_kj": float(total_work_kj),
        "kcal": float(total_kcal),
        "duration_min": float(duration_min),
        "distance_km": float(distance_km),
        "is_race": bool(is_race),
        "metric_name": metric_name,
        "metric_value": float(metric_value),
    }


def _factor_from_load(metric_value: float, is_race: bool) -> float:
    if metric_value <= 0:
        base = 0.90
    elif metric_value <= 30:
        base = 0.95
    elif metric_value <= 60:
        base = 1.00
    elif metric_value <= 90:
        base = 1.05
    elif metric_value <= 120:
        base = 1.12
    elif metric_value <= 160:
        base = 1.20
    else:
        base = 1.25

    if is_race:
        base = max(base, 1.25) + 0.05

    return max(0.88, min(1.35, base))


def _zone_from_factor(f: float, is_race: bool) -> str:
    if is_race or f >= 1.22:
        return "race/very_hard"
    if f >= 1.10:
        return "hard"
    if f >= 0.97:
        return "steady"
    if f >= 0.92:
        return "easy"
    return "rest"


def _compute_training_factors_for_week(db: Session, user_id: int, dates: list[str]) -> dict[str, dict[str, Any]]:
    unique_dates: list[str] = sorted({d for d in dates if d})
    meta_by_date: dict[str, dict[str, Any]] = {}
    values: list[float] = []

    for d in unique_dates:
        meta = _compute_training_load_for_date(db, user_id, d)
        meta_by_date[d] = meta
        values.append(max(0.0, meta.get("metric_value", 0.0)))

    non_zero = [v for v in values if v > 0]
    if not non_zero:
        logger.info("Training load: all days zero; returning neutral factors (1.0)")
        out: dict[str, dict[str, Any]] = {}
        for d in unique_dates:
            m = meta_by_date[d]
            out[d] = {
                "factor": 1.0,
                "metric_name": m["metric_name"],
                "metric_value": m["metric_value"],
                "score": m["score"],
                "is_race": m["is_race"],
                "zone": "steady",
            }
        return out

    median_non_zero = sorted(non_zero)[len(non_zero) // 2]
    logger.info("Training load: weekly median_non_zero_metric=%.2f", median_non_zero)

    out: dict[str, dict[str, Any]] = {}
    for d in unique_dates:
        m = meta_by_date[d]
        mv = max(0.0, m.get("metric_value", 0.0))
        factor = _factor_from_load(mv, m["is_race"])
        zone = _zone_from_factor(factor, m["is_race"])

        logger.info(
            "Training load: date=%s metric=%s value=%.2f factor=%.3f zone=%s is_race=%s",
            d,
            m["metric_name"],
            mv,
            factor,
            zone,
            m["is_race"],
        )

        out[d] = {
            "factor": factor,
            "metric_name": m["metric_name"],
            "metric_value": mv,
            "score": m["score"],
            "is_race": m["is_race"],
            "zone": zone,
        }

    return out


# ===========================
# LLM pick-or-create helper
# ===========================


def _day_uniqueness_required(slot_norm: str) -> bool:
    return slot_norm in _DAY_UNIQUE_SLOTS


def _normalize_key_set(keys: list[str]) -> set[str]:
    out: set[str] = set()
    for k in keys or []:
        s = (k or "").strip().lower()
        if s and s != "unknown":
            out.add(s)
    return out


_MEAL_TITLE_NOISE_WORDS = {
    "a",
    "an",
    "and",
    "bowl",
    "breakfast",
    "classic",
    "delight",
    "dinner",
    "for",
    "hearty",
    "lunch",
    "meal",
    "of",
    "parfait",
    "plate",
    "savory",
    "snack",
    "the",
    "with",
    "zesty",
}


def _meal_similarity_key(title: str | None) -> str:
    """Build a stable identity from a title, ignoring order and presentation words."""
    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    core = [token for token in tokens if token not in _MEAL_TITLE_NOISE_WORDS]
    return " ".join(sorted(core))


_FALLBACK_PROTEINS: dict[str, tuple[str, ...]] = {
    "vegan": (
        "tofu",
        "tempeh",
        "lentils",
        "chickpeas",
        "black beans",
        "kidney beans",
        "white beans",
        "split peas",
        "pea protein",
        "pumpkin seeds",
        "hemp seeds",
        "edamame",
        "seitan",
    ),
    "vegetarian": (
        "eggs",
        "greek yogurt",
        "cottage cheese",
        "tofu",
        "tempeh",
        "lentils",
        "chickpeas",
        "black beans",
        "kidney beans",
        "white beans",
        "split peas",
        "pea protein",
        "pumpkin seeds",
        "hemp seeds",
        "edamame",
        "seitan",
    ),
    "pescatarian": (
        "salmon",
        "tuna",
        "shrimp",
        "cod",
        "eggs",
        "greek yogurt",
        "cottage cheese",
        "tofu",
        "tempeh",
        "lentils",
        "chickpeas",
        "black beans",
        "kidney beans",
        "white beans",
        "split peas",
        "pea protein",
        "pumpkin seeds",
        "hemp seeds",
        "edamame",
        "seitan",
    ),
    "omnivore": (
        "chicken",
        "turkey",
        "salmon",
        "tuna",
        "shrimp",
        "cod",
        "eggs",
        "greek yogurt",
        "cottage cheese",
        "tofu",
        "tempeh",
        "lentils",
        "chickpeas",
        "black beans",
        "kidney beans",
        "white beans",
        "split peas",
        "pea protein",
        "pumpkin seeds",
        "hemp seeds",
        "edamame",
        "seitan",
        "beef",
        "pork",
    ),
}
_FALLBACK_CARBS: tuple[str, ...] = (
    "oats",
    "quinoa",
    "rice",
    "sweet potato",
    "potato",
    "corn",
    "buckwheat",
    "millet",
    "plantain",
    "pasta",
    "bread",
    "couscous",
)


def _fallback_protein_group(protein: str) -> str:
    if protein in {"salmon", "tuna", "shrimp", "cod"}:
        return "fish"
    if protein in {"chicken", "turkey"}:
        return "poultry"
    if protein == "beef":
        return "beef"
    if protein == "pork":
        return "pork"
    if protein == "eggs":
        return "eggs"
    if protein in {"greek yogurt", "cottage cheese"}:
        return "dairy"
    return "plant"


def _fallback_amount(protein: str) -> tuple[str, str]:
    if protein == "eggs":
        return "3", "items"
    if protein == "pea protein":
        return "1", "scoop"
    if protein in {"pumpkin seeds", "hemp seeds"}:
        return "1/2", "cup"
    if protein in {
        "greek yogurt",
        "cottage cheese",
        "lentils",
        "chickpeas",
        "black beans",
        "kidney beans",
        "white beans",
        "split peas",
        "edamame",
    }:
        return "1", "cup"
    return "6", "oz"


def _fallback_carb_amount(carb: str) -> tuple[str, str]:
    if carb in {"sweet potato", "potato", "plantain"}:
        return "8", "oz"
    if carb == "bread":
        return "2", "slices"
    if carb == "oats":
        return "3/4", "cup"
    return "1", "cup"


def _deterministic_fallback_idea(
    *,
    slot: str,
    tgt: MealTarget,
    primary_diet: str,
    ingredient_exclusions: list[str],
    used_protein_items: set[str],
    used_carb_items: set[str],
    used_meal_keys: set[str],
    banned_protein_groups: set[str],
) -> dict[str, Any] | None:
    """Build a measured, preference-safe meal when the AI provider is unavailable."""
    diet = primary_diet if primary_diet in _FALLBACK_PROTEINS else "omnivore"
    for protein in _FALLBACK_PROTEINS[diet]:
        protein_group = _fallback_protein_group(protein)
        if protein in used_protein_items or protein_group in banned_protein_groups:
            continue
        for carb in _FALLBACK_CARBS:
            if carb in used_carb_items:
                continue
            title = f"{protein.title()} and {carb.title()} {slot.title()}"
            if _meal_similarity_key(title) in used_meal_keys:
                continue
            protein_amount, protein_unit = _fallback_amount(protein)
            carb_amount, carb_unit = _fallback_carb_amount(carb)
            ingredients = [
                {"name": protein, "amount": protein_amount, "unit": protein_unit},
                {"name": carb, "amount": carb_amount, "unit": carb_unit},
                {"name": "spinach", "amount": "2", "unit": "cups"},
                {"name": "cherry tomatoes", "amount": "1", "unit": "cup"},
                {"name": "olive oil", "amount": "1", "unit": "tbsp"},
            ]
            searchable = f"{title} {json.dumps(ingredients)}"
            if _text_violates_exclusions(searchable, ingredient_exclusions):
                continue
            return {
                "title": title,
                "description": "A reliable measured meal selected from Glycofy's offline fallback library.",
                "ingredients": ingredients,
                "instructions": [
                    f"Cook the {carb} and prepare the {protein} until safely done.",
                    "Combine with the vegetables and olive oil, then season to taste.",
                ],
                "protein_group": protein_group,
                "protein_item": protein,
                "carb_item": carb.replace(" ", "_"),
                "approx_macros": {
                    "kcal": round(tgt.kcal),
                    "protein_g": round(tgt.protein_g),
                    "carbs_g": round(tgt.carbs_g),
                    "fat_g": round(tgt.fat_g),
                },
            }
    return None


def _llm_pick_or_create(
    client: ClientType,
    slot: str,
    tgt: MealTarget,
    candidates: list[tuple[Recipe, dict[str, float], float]],
    date: str | None,
    diet_tags: list[str] | None,
    primary_diet: str,
    user_pref: UserPreference | None,
    used_protein_items: list[str],
    used_carb_items: list[str],
    used_recipe_ids: set[int] | None,
    used_meal_keys: set[str] | None,
    allow_new_recipe: bool,
    banned_protein_groups: set[str] | None = None,
) -> tuple[str, Recipe | None, dict[str, float] | None, str, dict[str, Any], dict[str, Any] | None]:
    # Pre-compute best catalog candidate (for safe fallback)
    best_r: Recipe | None = None
    best_deltas: dict[str, float] | None = None
    if candidates:
        best_r, best_deltas, _ = candidates[0]

    slot_norm = _normalize_slot(slot)
    enforce_day_unique = _day_uniqueness_required(slot_norm)

    used_protein_set = _normalize_key_set(used_protein_items)
    used_carb_set = _normalize_key_set(used_carb_items)

    banned_groups = {pg.strip().lower() for pg in (banned_protein_groups or set()) if pg.strip()}

    # Ingredient exclusions from preferences
    # Use the same merged custom + structured allergy exclusions as catalog
    # filtering. AI-created and deterministic fallback meals must never have a
    # weaker safety policy than catalog picks.
    ingredient_exclusions = _preference_exclusions(user_pref)

    model = _openai_model()

    system_msg = (
        "You are an elite sports nutrition AI for endurance athletes.\n\n"
        "CONTEXT:\n"
        "- You receive a single MEAL SLOT at a time (e.g., breakfast, lunch, dinner, snack).\n"
        "- For that slot you are given:\n"
        "  - target_macros: kcal, protein_g, carbs_g, fat_g for this one meal.\n"
        "  - diet_tags: e.g. pescatarian, vegetarian, vegan, gluten_free.\n"
        "  - ingredient_exclusions: things the user does NOT want.\n"
        "  - candidate catalog recipes: optional, may be empty.\n"
        "  - used_protein_items_today: specific proteins already used today.\n"
        "  - used_carb_items_today: specific carbs already used today.\n"
        "  - used_recipe_ids_week: recipe IDs already used this week.\n"
        "  - used_meal_keys_week: normalized identities of meals already used this week.\n"
        "  - banned_protein_groups_slot_week: protein groups that are ALREADY used twice\n"
        "    for this slot in the current week — you MUST NOT use these groups again for this slot.\n"
        "  - allow_new_recipe: flag indicating whether you may invent a new recipe.\n\n"
        "GOAL (PER SLOT):\n"
        "- Choose the best meal for this slot by either:\n"
        '  1) PICKING a catalog recipe (mode="pick"); or\n'
        '  2) CREATING a new, simple recipe (mode="create").\n\n'
        "MACRO + ATHLETE RULES:\n"
        "- Hit macro targets within roughly ±15%, prioritizing PROTEIN and total KCAL.\n\n"
        "DAY VARIETY RULES (HARD FOR MAIN MEALS):\n"
        "- For main meals (breakfast/lunch/dinner):\n"
        "  - You MUST NOT reuse a protein_item that appears in used_protein_items_today.\n"
        "    Example: salmon + tuna + shrimp is OK; salmon + salmon is NOT OK.\n"
        "  - You MUST NOT reuse a carb_item that appears in used_carb_items_today.\n"
        "    Example: rice + quinoa + pasta is OK; oats + oats is NOT OK.\n"
        "- Snacks are flexible and MAY repeat if needed.\n\n"
        "RECIPE CREATION RULES (GUARDRAILS):\n"
        "- Use 5–7 main ingredients MAX (excluding pantry staples like salt, pepper, water, cooking oil).\n"
        "- EVERY ingredient must include a practical single-serving quantity and unit.\n"
        "- Prefer grams or ounces for proteins/starches and cups, tablespoons, teaspoons, or item counts where natural.\n"
        "- Never return a bare ingredient name such as 'spinach' or 'olive oil'.\n"
        "- Total active cooking time ~20–30 minutes.\n"
        "- Respect diet_tags + ingredient_exclusions strictly.\n"
        "- NEVER use a protein_group that appears in banned_protein_groups_slot_week for this slot.\n\n"
        "- NEVER create a recipe equivalent to an identity in used_meal_keys_week.\n\n"
        "OUTPUT FORMAT (STRICT JSON ONLY):\n"
        "{\n"
        '  "slot": "<slot name>",\n'
        '  "mode": "pick" | "create",\n'
        '  "pick_id": <int or null>,\n'
        '  "new_recipe": {\n'
        '    "title": "<string>",\n'
        '    "ingredients": [{"name": "<ingredient>", "amount": "<number or fraction>", "unit": "<g|oz|cup|tbsp|tsp|can|item>"}],\n'
        '    "instructions": ["<step 1>", "<step 2>", "..."],\n'
        '    "protein_group": "fish" | "poultry" | "beef" | "pork" | "eggs" | "dairy" | "plant" | "unknown",\n'
        '    "protein_item": "<specific protein like salmon, tuna, shrimp, chicken, tofu, eggs>",\n'
        '    "carb_item": "<specific carb like rice, quinoa, pasta, oats, potato, bread>",\n'
        '    "macro_estimate": {"kcal": <int>, "protein_g": <int>, "carbs_g": <int>, "fat_g": <int>}\n'
        "  },\n"
        '  "reason": "<short explanation>"\n'
        "}\n"
    )

    candidate_payloads: list[dict[str, Any]] = []
    for r, _d, _s in candidates:
        candidate_payloads.append(
            {
                "id": r.id,
                "title": r.title,
                "macro_estimate": {
                    "kcal": getattr(r, "kcal", None),
                    "protein_g": getattr(r, "protein_g", None),
                    "carbs_g": getattr(r, "carbs_g", None),
                    "fat_g": getattr(r, "fat_g", None),
                },
                "diet_tags": _coerce_tag_list(getattr(r, "diet_tags", None)),
                "protein_group": _guess_protein_group_for_recipe(r),
                "protein_item": _guess_protein_item_for_recipe(r),
                "carb_item": _guess_carb_item_for_recipe(r),
            }
        )

    used_ids = sorted(list(used_recipe_ids)) if used_recipe_ids else []

    user_payload: dict[str, Any] = {
        "date": date,
        "slot": slot,
        "primary_diet": primary_diet,
        "diet_tags": diet_tags or [],
        "target_macros": tgt.model_dump(),
        "used_protein_items_today": used_protein_items,
        "used_carb_items_today": used_carb_items,
        "disallowed_protein_items_today": sorted(list(used_protein_set)) if enforce_day_unique else [],
        "disallowed_carb_items_today": sorted(list(used_carb_set)) if enforce_day_unique else [],
        "used_recipe_ids_week": used_ids,
        "used_meal_keys_week": sorted(used_meal_keys or set()),
        "banned_protein_groups_slot_week": sorted(list(banned_groups)),
        "allow_new_recipe": bool(allow_new_recipe),
        "ingredient_exclusions": ingredient_exclusions,
        "candidates": candidate_payloads,
    }

    def _call_llm(extra_strict: bool, violated_kind: str | None = None, violated_value: str | None = None):
        if not extra_strict:
            return _safe_openai_json_pick(client, model, system_msg, user_payload)

        strict_system = system_msg + (
            "\n\nIMPORTANT (RETRY):\n"
            "- Your previous selection violated a required recipe rule.\n"
            f"- violated_kind={violated_kind!r} violated_value={violated_value!r}\n"
            "- If violated_kind is ingredient_quantities, return every ingredient as an object with name, amount, and unit.\n"
            "- If violated_kind is ingredient_exclusion, remove the excluded ingredient and all related foods; do not substitute another form of it.\n"
            "- Otherwise avoid disallowed_*_items_today and every identity in used_meal_keys_week.\n"
            "- Regenerate now and correct the stated violation.\n"
        )
        strict_payload = dict(user_payload)
        strict_payload["retry_due_to_violation"] = violated_kind or "day_variety"
        if violated_kind:
            strict_payload["violated_kind"] = violated_kind
        if violated_value:
            strict_payload["violated_value"] = violated_value
        return _safe_openai_json_pick(client, model, strict_system, strict_payload)

    data, meta = _call_llm(extra_strict=False)
    meta.setdefault("timestamp", datetime.now(UTC).isoformat())

    if not data:
        if best_r is not None:
            meta.setdefault("mode", "pick")
            return "pick", best_r, best_deltas, "default: lowest macro delta", meta, None
        fallback_idea = _deterministic_fallback_idea(
            slot=slot_norm,
            tgt=tgt,
            primary_diet=primary_diet,
            ingredient_exclusions=ingredient_exclusions,
            used_protein_items=used_protein_set,
            used_carb_items=used_carb_set,
            used_meal_keys=used_meal_keys or set(),
            banned_protein_groups=banned_groups,
        )
        if fallback_idea is not None:
            meta.update({"mode": "create", "fallback": "deterministic_library"})
            return (
                "create",
                None,
                None,
                "Reliable offline meal selected while AI generation was unavailable.",
                meta,
                fallback_idea,
            )
        meta.setdefault("mode", "empty")
        return "empty", None, None, "No recipes or AI ideas available.", meta, None

    # --- parse ---
    mode = str(data.get("mode") or "").strip().lower()
    pick_id_raw = data.get("pick_id", None)
    new_recipe = data.get("new_recipe", None)
    reason = str(data.get("reason", "chosen by LLM"))

    if not mode:
        if pick_id_raw is not None:
            mode = "pick"
        elif new_recipe:
            mode = "create"
        else:
            mode = "pick"

    def _violates_day_unique(protein_item: str | None, carb_item: str | None) -> tuple[bool, str | None, str | None]:
        if not enforce_day_unique:
            return (False, None, None)
        pi = (protein_item or "").strip().lower()
        ci = (carb_item or "").strip().lower()
        if pi and pi != "unknown" and pi in used_protein_set:
            return (True, "protein_item", pi)
        if ci and ci != "unknown" and ci in used_carb_set:
            return (True, "carb_item", ci)
        return (False, None, None)

    # --- PICK ---
    if mode == "pick":
        try:
            pick_id = int(pick_id_raw)
        except Exception:
            if best_r is not None:
                meta["fallback"] = "invalid_pick_payload"
                meta["mode"] = "pick"
                return "pick", best_r, best_deltas, "default: lowest macro delta", meta, None
            meta["mode"] = "empty"
            return "empty", None, None, "No valid pick_id and no candidates.", meta, None

        for r, deltas, _s in candidates:
            if int(r.id) == pick_id:
                # enforce day variety server-side (protein_item + carb_item)
                pi = _guess_protein_item_for_recipe(r)
                ci = _guess_carb_item_for_recipe(r)
                violated, kind, val = _violates_day_unique(pi, ci)
                if violated:
                    # one retry
                    data2, meta2 = _call_llm(extra_strict=True, violated_kind=kind, violated_value=val)
                    meta2.setdefault("timestamp", datetime.now(UTC).isoformat())
                    meta2["retry"] = "day_variety"
                    if data2 and isinstance(data2, dict):
                        # overwrite and re-parse once
                        data = data2
                        meta = {**meta, **meta2}
                        mode = str(data.get("mode") or "").strip().lower() or "pick"
                        pick_id_raw = data.get("pick_id", None)
                        new_recipe = data.get("new_recipe", None)
                        reason = str(data.get("reason", reason))

                        if mode == "pick":
                            try:
                                pick_id = int(pick_id_raw)
                            except Exception:
                                break
                            for r2, deltas2, _s2 in candidates:
                                if int(r2.id) == pick_id:
                                    pi2 = _guess_protein_item_for_recipe(r2)
                                    ci2 = _guess_carb_item_for_recipe(r2)
                                    violated2, _k2, _v2 = _violates_day_unique(pi2, ci2)
                                    if violated2:
                                        meta["fallback"] = "day_variety_repeat_after_retry"
                                        if best_r is not None:
                                            meta["mode"] = "pick"
                                            return (
                                                "pick",
                                                best_r,
                                                best_deltas,
                                                "default: day variety fallback",
                                                meta,
                                                None,
                                            )
                                        meta["mode"] = "empty"
                                        return "empty", None, None, "Unable to satisfy day variety rule.", meta, None
                                    meta["mode"] = "pick"
                                    return "pick", r2, deltas2, reason, meta, None
                        # if retry not valid pick, fall through to general handling below

                    meta["fallback"] = "day_variety_repeat"
                    if best_r is not None:
                        meta["mode"] = "pick"
                        return "pick", best_r, best_deltas, "default: day variety fallback", meta, None
                    meta["mode"] = "empty"
                    return "empty", None, None, "Unable to satisfy day variety rule.", meta, None

                meta["mode"] = "pick"
                return "pick", r, deltas, reason, meta, None

        if best_r is not None:
            meta["fallback"] = "pick_not_in_candidates"
            meta["mode"] = "pick"
            return "pick", best_r, best_deltas, "default: lowest macro delta", meta, None

        meta["mode"] = "empty"
        return "empty", None, None, "No valid pick_id and no candidates.", meta, None

    # --- CREATE ---
    if mode == "create":
        if not isinstance(new_recipe, dict):
            if best_r is not None:
                meta["fallback"] = "invalid_new_recipe_payload"
                meta["mode"] = "pick"
                return "pick", best_r, best_deltas, "default: lowest macro delta", meta, None
            meta["mode"] = "empty"
            return "empty", None, None, "Invalid new_recipe and no candidates.", meta, None

        title = str(new_recipe.get("title", "AI-created meal")).strip() or "AI-created meal"
        ingredients = new_recipe.get("ingredients") or []
        instructions = new_recipe.get("instructions") or []
        macro_est = new_recipe.get("macro_estimate") or {}

        protein_group = str(new_recipe.get("protein_group", "") or "").strip().lower() or "unknown"
        protein_item = str(new_recipe.get("protein_item", "") or "").strip().lower() or "unknown"
        carb_item = str(new_recipe.get("carb_item", "") or "").strip().lower() or "unknown"

        if not _ingredients_have_quantities(ingredients):
            data2, meta2 = _call_llm(
                extra_strict=True,
                violated_kind="ingredient_quantities",
                violated_value="one or more ingredients had no amount/unit",
            )
            nr2 = data2.get("new_recipe") if isinstance(data2, dict) else None
            if isinstance(nr2, dict) and _ingredients_have_quantities(nr2.get("ingredients") or []):
                new_recipe = nr2
                title = str(nr2.get("title", title)).strip() or title
                ingredients = nr2.get("ingredients") or []
                instructions = nr2.get("instructions") or instructions
                macro_est = nr2.get("macro_estimate") or macro_est
                protein_group = str(nr2.get("protein_group", protein_group) or protein_group).strip().lower()
                protein_item = str(nr2.get("protein_item", protein_item) or protein_item).strip().lower()
                carb_item = str(nr2.get("carb_item", carb_item) or carb_item).strip().lower()
                reason = str(data2.get("reason", reason))
                meta = {**meta, **meta2, "retry": "ingredient_quantities"}
            elif best_r is not None:
                meta["fallback"] = "missing_ingredient_quantities"
                meta["mode"] = "pick"
                return "pick", best_r, best_deltas, "default: complete catalog recipe", meta, None
            else:
                meta["mode"] = "empty"
                return "empty", None, None, "Unable to generate ingredients with quantities.", meta, None

        if _text_violates_exclusions(json.dumps(ingredients, default=str), ingredient_exclusions):
            data2, meta2 = _call_llm(
                extra_strict=True,
                violated_kind="ingredient_exclusion",
                violated_value=", ".join(ingredient_exclusions),
            )
            nr2 = data2.get("new_recipe") if isinstance(data2, dict) else None
            nr2_ingredients = nr2.get("ingredients") if isinstance(nr2, dict) else None
            if (
                isinstance(nr2, dict)
                and _ingredients_have_quantities(nr2_ingredients)
                and not _text_violates_exclusions(json.dumps(nr2_ingredients, default=str), ingredient_exclusions)
            ):
                new_recipe = nr2
                title = str(nr2.get("title", title)).strip() or title
                ingredients = nr2_ingredients
                instructions = nr2.get("instructions") or instructions
                macro_est = nr2.get("macro_estimate") or macro_est
                protein_group = str(nr2.get("protein_group", protein_group) or protein_group).strip().lower()
                protein_item = str(nr2.get("protein_item", protein_item) or protein_item).strip().lower()
                carb_item = str(nr2.get("carb_item", carb_item) or carb_item).strip().lower()
                reason = str(data2.get("reason", reason))
                meta = {**meta, **meta2, "retry": "ingredient_exclusion"}
            elif best_r is not None:
                meta["fallback"] = "ingredient_exclusion"
                meta["mode"] = "pick"
                return "pick", best_r, best_deltas, "default: exclusion-safe catalog recipe", meta, None
            else:
                meta["mode"] = "empty"
                return "empty", None, None, "Unable to satisfy ingredient exclusions.", meta, None

        banned_groups2 = {pg.strip().lower() for pg in (banned_protein_groups or set()) if pg.strip()}
        if banned_groups2 and protein_group in banned_groups2:
            meta["fallback"] = "protein_group_week_cap"
            if best_r is not None:
                meta["mode"] = "pick"
                return "pick", best_r, best_deltas, "default: weekly protein cap fallback", meta, None
            meta["mode"] = "empty"
            return "empty", None, None, "All protein groups for this slot are capped this week.", meta, None

        violated, kind, val = _violates_day_unique(protein_item, carb_item)
        meal_key = _meal_similarity_key(title)
        if not violated and meal_key and used_meal_keys and meal_key in used_meal_keys:
            violated, kind, val = True, "meal_identity", meal_key
        if violated:
            data2, meta2 = _call_llm(extra_strict=True, violated_kind=kind, violated_value=val)
            meta2.setdefault("timestamp", datetime.now(UTC).isoformat())
            meta2["retry"] = "day_variety"
            if data2 and isinstance(data2, dict):
                mode2 = str(data2.get("mode") or "").strip().lower()
                nr2 = data2.get("new_recipe", None)
                reason2 = str(data2.get("reason", reason))
                if mode2 == "create" and isinstance(nr2, dict):
                    pg2 = str(nr2.get("protein_group", "") or "").strip().lower() or protein_group
                    pi2 = str(nr2.get("protein_item", "") or "").strip().lower() or _guess_protein_item_from_text(
                        str(nr2)
                    )
                    ci2 = str(nr2.get("carb_item", "") or "").strip().lower() or _guess_carb_item_from_text(str(nr2))
                    title2 = str(nr2.get("title", title)).strip() or title
                    meal_key2 = _meal_similarity_key(title2)

                    violated2, _k2, _v2 = _violates_day_unique(pi2, ci2)
                    duplicate_title2 = bool(meal_key2 and used_meal_keys and meal_key2 in used_meal_keys)
                    if not violated2 and not duplicate_title2 and (not banned_groups2 or pg2 not in banned_groups2):
                        title = title2
                        ingredients = nr2.get("ingredients") or ingredients
                        instructions = nr2.get("instructions") or instructions
                        macro_est = nr2.get("macro_estimate") or macro_est
                        protein_group = pg2
                        protein_item = pi2 or protein_item
                        carb_item = ci2 or carb_item
                        reason = reason2
                        meta = {**meta, **meta2}
                    else:
                        meta = {**meta, **meta2}
                        meta["fallback"] = "day_variety_repeat_after_retry"
                        if best_r is not None:
                            meta["mode"] = "pick"
                            return "pick", best_r, best_deltas, "default: day variety fallback", meta, None
                        meta["mode"] = "empty"
                        return "empty", None, None, "Unable to satisfy day variety rule.", meta, None
                else:
                    meta = {**meta, **meta2}
                    meta["fallback"] = "day_variety_retry_noncreate"
                    if best_r is not None:
                        meta["mode"] = "pick"
                        return "pick", best_r, best_deltas, "default: day variety fallback", meta, None
                    meta["mode"] = "empty"
                    return "empty", None, None, "Unable to satisfy day variety rule.", meta, None
            else:
                meta["fallback"] = "day_variety_repeat"
                if best_r is not None:
                    meta["mode"] = "pick"
                    return "pick", best_r, best_deltas, "default: day variety fallback", meta, None
                meta["mode"] = "empty"
                return "empty", None, None, "Unable to satisfy day variety rule.", meta, None

        deltas: dict[str, float] = {}
        for m in _MACROS:
            target_v = float(getattr(tgt, m, 0.0))
            approx_v = float(macro_est.get(m, target_v))
            deltas[m] = abs(approx_v - target_v)

        ai_idea_payload = {
            "slot": slot,
            "title": title,
            "description": None,
            "ingredients": ingredients,
            "instructions": instructions,
            "approx_macros": {
                "kcal": float(macro_est.get("kcal", getattr(tgt, "kcal", 0.0))),
                "protein_g": float(macro_est.get("protein_g", getattr(tgt, "protein_g", 0.0))),
                "carbs_g": float(macro_est.get("carbs_g", getattr(tgt, "carbs_g", 0.0))),
                "fat_g": float(macro_est.get("fat_g", getattr(tgt, "fat_g", 0.0))),
            },
            "protein_group": protein_group or None,
            "protein_item": protein_item or None,
            "carb_item": carb_item or None,
        }

        meta["mode"] = "create"
        return "create", None, deltas, reason, meta, ai_idea_payload

    # Unknown mode fallback
    if best_r is not None:
        meta["fallback"] = "unknown_mode"
        meta["mode"] = "pick"
        return "pick", best_r, best_deltas, "default: lowest macro delta", meta, None

    meta["mode"] = "empty"
    return "empty", None, None, "Unknown mode and no candidates.", meta, None


# ===========================
# Simple in-memory cache (24h TTL)
# ===========================


class _Cache:
    def __init__(self) -> None:
        self.data: dict[str, tuple[float, dict[str, Any]]] = {}

    def _ttl(self) -> float:
        try:
            return float(os.environ.get("LLM_CACHE_TTL_SEC", "86400"))
        except Exception:
            return 86400.0

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        rec = self.data.get(key)
        if not rec:
            return None
        ts, val = rec
        if (now - ts) > self._ttl():
            self.data.pop(key, None)
            return None
        return val

    def set(self, key: str, val: dict[str, Any]) -> None:
        self.data[key] = (time.time(), val)


_CACHE = _Cache()


def _cache_key(
    user_id: int,
    req: RecommendRequest,
    pref_tags: list[str],
    week_meal_keys: set[str] | None = None,
    nutrition_fingerprint: dict[str, Any] | None = None,
) -> str:
    blob = json.dumps(
        {
            "u": user_id,
            "date": req.date,
            "totals": req.totals or {},
            "meals": [m.model_dump() for m in req.meals],
            "diet_tags": (req.diet_tags or []),
            "pref_tags": pref_tags,
            "week_meal_keys": sorted(week_meal_keys or set()),
            "nutrition": nutrition_fingerprint or {},
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ===========================
# Per-user/IP rate limiting (in-memory)
# ===========================


class _Rate:
    def __init__(self) -> None:
        self.users: dict[int, list[float]] = {}
        self.ips: dict[str, list[float]] = {}

    def _limits(self) -> tuple[int, int, int]:
        try:
            u = int(os.environ.get("LLM_RATE_MAX_PER_USER", "30"))
            i = int(os.environ.get("LLM_RATE_MAX_PER_IP", "60"))
            w = int(os.environ.get("LLM_RATE_WINDOW_SEC", "3600"))
            return u, i, w
        except Exception:
            return 30, 60, 3600

    def _trim(self, arr: list[float], window: int) -> None:
        cutoff = time.time() - window
        while arr and arr[0] < cutoff:
            arr.pop(0)

    def check_and_add(self, user_id: int, ip: str) -> None:
        u_max, i_max, window = self._limits()
        u_arr = self.users.setdefault(user_id, [])
        self._trim(u_arr, window)
        if len(u_arr) >= u_max:
            raise HTTPException(status_code=429, detail="Rate limit exceeded (user)")
        u_arr.append(time.time())

        ip_arr = self.ips.setdefault(ip, [])
        self._trim(ip_arr, window)
        if len(ip_arr) >= i_max:
            raise HTTPException(status_code=429, detail="Rate limit exceeded (ip)")
        ip_arr.append(time.time())


_RATE = _Rate()


# ===========================
# Weekly protein usage helpers (for Today(AI) weekly cap)
# ===========================


def _parse_iso_date(s: str | None) -> date:
    if not s:
        return datetime.now(UTC).date()
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()  # type: ignore[arg-type]
        except Exception:
            return datetime.now(UTC).date()


def _week_span_for_date(target: date) -> tuple[date, date]:
    weekday = target.weekday()  # Monday = 0
    start = target - timedelta(days=weekday)
    end = start + timedelta(days=6)
    return start, end


def _get_week_used_recipe_ids(db: Session, user: User, target_date: date) -> set[int]:
    ws, we = _week_span_for_date(target_date)
    rows = (
        db.query(PlanMeal.recipe_id)
        .join(Plan, PlanMeal.plan_id == Plan.id)
        .filter(
            Plan.user_id == user.id,
            Plan.date >= ws,
            Plan.date <= we,
            Plan.date != target_date,
            PlanMeal.recipe_id.isnot(None),
        )
        .all()
    )
    out: set[int] = set()
    for (rid,) in rows:
        try:
            out.add(int(rid))
        except Exception:
            continue
    logger.info(
        "LLM week_used_recipe_ids: user_id=%s target_date=%s week_span=(%s,%s) count=%d",
        user.id,
        target_date.isoformat(),
        ws.isoformat(),
        we.isoformat(),
        len(out),
    )
    return out


def _get_week_used_meal_keys(db: Session, user: User, target_date: date) -> set[str]:
    """Return semantic meal identities used on other days in the calendar week."""
    ws, we = _week_span_for_date(target_date)
    rows = (
        db.query(PlanMeal.title)
        .join(Plan, PlanMeal.plan_id == Plan.id)
        .filter(
            Plan.user_id == user.id,
            Plan.date >= ws,
            Plan.date <= we,
            Plan.date != target_date,
            PlanMeal.title.isnot(None),
        )
        .all()
    )
    meal_keys = {_meal_similarity_key(title) for (title,) in rows}
    meal_keys.discard("")
    logger.info(
        "LLM week_used_meal_keys: user_id=%s target_date=%s week_span=(%s,%s) count=%d",
        user.id,
        target_date.isoformat(),
        ws.isoformat(),
        we.isoformat(),
        len(meal_keys),
    )
    return meal_keys


def _get_week_protein_counts(db: Session, user: User, target_date: date) -> dict[tuple[str, str], int]:
    ws, we = _week_span_for_date(target_date)
    rows = (
        db.query(Plan.date, PlanMeal.meal_type, Recipe)
        .join(Plan, PlanMeal.plan_id == Plan.id)
        .outerjoin(Recipe, PlanMeal.recipe_id == Recipe.id)
        .filter(
            Plan.user_id == user.id,
            Plan.date >= ws,
            Plan.date <= we,
            Plan.date != target_date,
            PlanMeal.recipe_id.isnot(None),
        )
        .all()
    )

    counts: dict[tuple[str, str], int] = {}
    for plan_date, meal_type, recipe in rows:
        slot = _normalize_slot(meal_type)
        if not slot or recipe is None:
            continue
        pg_raw = getattr(recipe, "protein_group", None)
        if not pg_raw:
            pg_raw = _guess_protein_group_for_recipe(recipe)
        pg = (pg_raw or "").strip().lower()
        if not pg or pg == "unknown":
            continue
        key = (slot, pg)
        counts[key] = counts.get(key, 0) + 1

    logger.info(
        "LLM week_protein_counts: user_id=%s target_date=%s week_span=(%s,%s) entries=%s",
        user.id,
        target_date.isoformat(),
        ws.isoformat(),
        we.isoformat(),
        counts,
    )
    return counts


# ===========================
# Shared per-slot recommender (used by daily + weekly)
# ===========================


def _recommend_for_single_meal(
    client: ClientType,
    db: Session,
    date: str | None,
    tgt: MealTarget,
    diet_tags: list[str] | None,
    primary_diet: str,
    pref: UserPreference | None,
    provider: str,
    used_protein_items: list[str],
    used_carb_items: list[str],
    used_recipe_ids: set[int] | None = None,
    used_meal_keys: set[str] | None = None,
    allow_new_recipe: bool = True,
    week_protein_counts: dict[tuple[str, str], int] | None = None,
    protein_cap_per_slot: int = 2,
    prefer_fast_catalog: bool = False,
) -> SlotRecommendation:
    logger.info("LLM recommend-slot: date=%s slot=%s target_macros=%s", date, tgt.slot, tgt.model_dump())

    slot_norm = _normalize_slot(tgt.slot)
    ingredient_exclusions = _preference_exclusions(pref)

    # Weekly cap (per-slot) remains protein_group-based
    banned_groups_week: set[str] = set()
    if week_protein_counts:
        for (slot_key, pg), count in week_protein_counts.items():
            if slot_key == slot_norm and count >= protein_cap_per_slot:
                banned_groups_week.add(pg)
        if banned_groups_week:
            logger.info(
                "LLM recommend-slot: date=%s slot=%s weekly protein cap reached for groups=%s",
                date,
                slot_norm,
                sorted(list(banned_groups_week)),
            )

    # Day-level uniqueness across main meals (protein_item + carb_item)
    banned_protein_items_day: set[str] = set()
    banned_carb_items_day: set[str] = set()
    if _day_uniqueness_required(slot_norm):
        banned_protein_items_day = _normalize_key_set(used_protein_items)
        banned_carb_items_day = _normalize_key_set(used_carb_items)
        if banned_protein_items_day or banned_carb_items_day:
            logger.info(
                "LLM recommend-slot: date=%s slot=%s day variety enforced; disallowed_protein_items=%s disallowed_carb_items=%s",
                date,
                slot_norm,
                sorted(list(banned_protein_items_day)),
                sorted(list(banned_carb_items_day)),
            )

    candidates = _top_k_candidates(
        db=db,
        slot=tgt.slot,
        tgt=tgt,
        diet_tags=diet_tags,
        primary_diet=primary_diet,
        k=6,
        exclude_ids=used_recipe_ids,
        disallowed_protein_groups=banned_groups_week or None,
        disallowed_protein_items=banned_protein_items_day or None,
        disallowed_carb_items=banned_carb_items_day or None,
        disallowed_meal_keys=used_meal_keys,
        ingredient_exclusions=ingredient_exclusions,
    )
    # Legacy catalog rows may contain only bare ingredient names. Selecting
    # those would produce a meal that cannot be cooked or shopped accurately.
    candidates = [candidate for candidate in candidates if _recipe_has_quantified_ingredients(candidate[0])]

    # The weekly protein-group cap is a variety preference, not a reason to
    # discard an otherwise complete week. Late in a seven-day run it is
    # possible for every catalog option in a slot to be capped. In that case,
    # relax only the weekly cap while preserving diet, ingredient, recipe, and
    # same-day uniqueness constraints.
    weekly_cap_relaxed = False
    if not candidates and banned_groups_week:
        logger.warning(
            "LLM recommend-slot: date=%s slot=%s exhausted weekly protein groups; relaxing cap",
            date,
            slot_norm,
        )
        banned_groups_week = set()
        weekly_cap_relaxed = True
        candidates = _top_k_candidates(
            db=db,
            slot=tgt.slot,
            tgt=tgt,
            diet_tags=diet_tags,
            primary_diet=primary_diet,
            k=6,
            exclude_ids=used_recipe_ids,
            disallowed_protein_groups=None,
            disallowed_protein_items=banned_protein_items_day or None,
            disallowed_carb_items=banned_carb_items_day or None,
            disallowed_meal_keys=used_meal_keys,
            ingredient_exclusions=ingredient_exclusions,
        )
        candidates = [candidate for candidate in candidates if _recipe_has_quantified_ingredients(candidate[0])]

    result = None
    if prefer_fast_catalog and candidates:
        picked, deltas, _score = candidates[0]
        within_tolerance = all(
            _safe_float(getattr(tgt, name, 0.0)) <= 0
            or abs(_safe_float(getattr(picked, name, 0.0)) - _safe_float(getattr(tgt, name, 0.0)))
            / _safe_float(getattr(tgt, name, 0.0))
            <= 0.25
            for name in _MACROS
        )
        if within_tolerance:
            result = (
                "pick",
                picked,
                deltas,
                "Best complete recipe match for your meal targets, diet, and weekly variety.",
                {"provider": "catalog", "mode": "pick", "fast_path": True},
                None,
            )

    if result is None:
        for attempt in range(1, _SLOT_RECOMMENDATION_ATTEMPTS + 1):
            result = _llm_pick_or_create(
                client=client,
                slot=tgt.slot,
                tgt=tgt,
                candidates=candidates,
                date=date,
                diet_tags=diet_tags,
                primary_diet=primary_diet,
                user_pref=pref,
                used_protein_items=used_protein_items,
                used_carb_items=used_carb_items,
                used_recipe_ids=used_recipe_ids,
                used_meal_keys=used_meal_keys,
                allow_new_recipe=allow_new_recipe,
                banned_protein_groups=banned_groups_week or None,
            )
            mode, picked_recipe, _deltas, _reason, _meta, ai_idea_payload = result
            if mode != "empty" and (picked_recipe is not None or ai_idea_payload):
                if attempt > 1:
                    _meta["slot_retry_attempts"] = attempt
                break
            logger.warning(
                "LLM recommend-slot: date=%s slot=%s attempt=%d returned empty; retrying",
                date,
                tgt.slot,
                attempt,
            )

    assert result is not None
    mode, picked_recipe, deltas, reason, pick_meta, ai_idea_payload = result

    pick_meta.setdefault("timestamp", datetime.now(UTC).isoformat())
    pick_meta.setdefault("diet_tags", diet_tags or [])
    pick_meta.setdefault("provider", pick_meta.get("provider", provider))
    if weekly_cap_relaxed:
        pick_meta["weekly_protein_cap_relaxed"] = True

    target_dict = {
        "kcal": tgt.kcal,
        "protein_g": tgt.protein_g,
        "carbs_g": tgt.carbs_g,
        "fat_g": tgt.fat_g,
    }

    if mode == "empty" or (picked_recipe is None and not ai_idea_payload):
        return SlotRecommendation(
            slot=tgt.slot,
            target=target_dict,
            recipe=None,
            deltas=None,
            reason=reason,
            meta={**pick_meta, "mode": "empty"},
            ai_idea=None,
        )

    # Track variety keys only for main meals (snack flexible)
    enforce_day = _day_uniqueness_required(slot_norm)

    if mode == "create" and ai_idea_payload:
        approx = ai_idea_payload.get("approx_macros") or {}
        if deltas is None:
            deltas = {}
            for m in _MACROS:
                target_v = float(getattr(tgt, m, 0.0))
                approx_v = float(approx.get(m, target_v))
                deltas[m] = abs(approx_v - target_v)

        pg = ai_idea_payload.get("protein_group") or "unknown"
        pi = ai_idea_payload.get("protein_item") or "unknown"
        ci = ai_idea_payload.get("carb_item") or "unknown"
        meal_key = _meal_similarity_key(str(ai_idea_payload.get("title") or ""))
        if used_meal_keys is not None and meal_key:
            used_meal_keys.add(meal_key)

        if enforce_day:
            if isinstance(pi, str) and pi and pi != "unknown":
                used_protein_items.append(pi)
            if isinstance(ci, str) and ci and ci != "unknown":
                used_carb_items.append(ci)

        # Weekly counts are protein_group-based
        if week_protein_counts is not None and isinstance(pg, str) and pg and pg != "unknown":
            key = (slot_norm, pg)
            week_protein_counts[key] = week_protein_counts.get(key, 0) + 1

        meta_with_ai = {
            **pick_meta,
            "mode": "create",
            "protein_group": pg,
            "protein_item": pi,
            "carb_item": ci,
            "ai_idea": ai_idea_payload,
        }
        return SlotRecommendation(
            slot=tgt.slot,
            target=target_dict,
            recipe=None,
            deltas={k: float(deltas.get(k, 0.0)) for k in _MACROS},
            reason=reason,
            meta=meta_with_ai,
            ai_idea=ai_idea_payload,
        )

    assert picked_recipe is not None, "picked_recipe should not be None in 'pick' mode"

    if used_recipe_ids is not None:
        try:
            used_recipe_ids.add(int(picked_recipe.id))
        except Exception:
            pass
    meal_key = _meal_similarity_key(getattr(picked_recipe, "title", None))
    if used_meal_keys is not None and meal_key:
        used_meal_keys.add(meal_key)

    pg = _guess_protein_group_for_recipe(picked_recipe)
    pi = _guess_protein_item_for_recipe(picked_recipe)
    ci = _guess_carb_item_for_recipe(picked_recipe)

    if enforce_day:
        if pi and pi != "unknown":
            used_protein_items.append(pi)
        if ci and ci != "unknown":
            used_carb_items.append(ci)

    if week_protein_counts is not None and pg and pg != "unknown":
        key = (slot_norm, pg)
        week_protein_counts[key] = week_protein_counts.get(key, 0) + 1

    pick_meta.setdefault("mode", "pick")
    pick_meta.setdefault("protein_group", pg)
    pick_meta.setdefault("protein_item", pi)
    pick_meta.setdefault("carb_item", ci)

    if deltas is None:
        _score, deltas_calc = _score_recipe_vs_target(picked_recipe, tgt)
        deltas = deltas_calc

    return SlotRecommendation(
        slot=tgt.slot,
        target=target_dict,
        recipe=_recipe_pick_from_model(picked_recipe),
        deltas={k: float(deltas.get(k, 0.0)) for k in _MACROS},
        reason=reason,
        meta=pick_meta,
        ai_idea=None,
    )


def _missing_recommendation_slots(items: list[SlotRecommendation]) -> list[str]:
    """Return requested slots that do not contain an applicable meal."""
    return [_normalize_slot(item.slot) for item in items if item.recipe is None and not item.ai_idea]


_LEADING_QUANTITY_RE = re.compile(r"^\s*(?:\d|[¼½¾⅓⅔⅛⅜⅝⅞])")
_UNQUANTIFIED_PANTRY_MARKERS = {
    "cooking oil",
    "fresh herbs",
    "herbs",
    "oil",
    "pepper",
    "salt",
    "water",
}


def _is_unquantified_pantry_item(name: str) -> bool:
    """Allow conventional pantry-to-taste items without weakening core quantities."""
    normalized = re.sub(r"[^a-z ]", " ", (name or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return any(re.search(rf"\b{re.escape(marker)}\b", normalized) for marker in _UNQUANTIFIED_PANTRY_MARKERS)


def _ingredients_have_quantities(ingredients: Any) -> bool:
    """Require every generated ingredient to be cookable, not merely named."""
    if not isinstance(ingredients, list) or not ingredients:
        return False
    for ingredient in ingredients:
        if isinstance(ingredient, dict):
            name = str(ingredient.get("name") or ingredient.get("ingredient") or "").strip()
            amount = str(ingredient.get("amount") or ingredient.get("qty") or ingredient.get("quantity") or "").strip()
            unit = str(ingredient.get("unit") or "").strip()
            if not name:
                return False
            # Older catalog rows store the full measured ingredient in `name`
            # (for example, "1 cup cooked quinoa"). Treat that as quantified.
            if _LEADING_QUANTITY_RE.match(name):
                continue
            if amount and unit:
                continue
            if _is_unquantified_pantry_item(name):
                continue
            return False
        elif isinstance(ingredient, str):
            # Retain compatibility with catalog-style strings such as
            # "1 cup Greek yogurt", while rejecting bare names like "spinach".
            if not _LEADING_QUANTITY_RE.match(ingredient) and not _is_unquantified_pantry_item(ingredient):
                return False
        else:
            return False
    return True


def _recipe_has_quantified_ingredients(recipe: Recipe) -> bool:
    """Catalog recipes are selectable only when every ingredient is usable."""
    ingredients = getattr(recipe, "ingredients", None) or []
    if isinstance(ingredients, str):
        ingredients = [line.strip() for line in ingredients.splitlines() if line.strip()]
    return _ingredients_have_quantities(ingredients)


# ===========================
# Persistence helpers for weekly apply
# ===========================


def _coerce_ai_ingredients_to_storage(ingredients: Any) -> Any:
    """
    Try to store AI-created ingredients in a way that's compatible with existing Recipe schema.
    If your Recipe.ingredients expects a JSON-able structure, this is fine.
    """
    if ingredients is None:
        return []
    if isinstance(ingredients, (list, tuple)):
        # Convert strings to named items while preserving already-structured
        # ingredients (including quantities and units).
        out: list[Any] = []
        for x in ingredients:
            if isinstance(x, dict):
                name = str(x.get("name") or x.get("ingredient") or x.get("item") or "").strip()
                if name:
                    out.append({**x, "name": name})
            else:
                s = str(x).strip()
                if s:
                    out.append({"name": s})
        return out
    # If it's a string or other, store as a single name
    s = str(ingredients).strip()
    return [{"name": s}] if s else []


def _coerce_ai_instructions_to_text(steps: Any) -> str:
    if steps is None:
        return ""
    if isinstance(steps, (list, tuple)):
        cleaned = [str(x).strip() for x in steps if str(x).strip()]
        return "\n".join(cleaned)
    return str(steps).strip()


def _get_or_create_plan(db: Session, user_id: int, day: date) -> Plan:
    plan = db.query(Plan).filter(Plan.user_id == user_id, Plan.date == day).first()
    if plan:
        return plan
    plan = Plan(user_id=user_id, date=day, locked=False, source="heuristic")
    db.add(plan)
    db.flush()
    return plan


def _ensure_plan_meals(db: Session, plan: Plan) -> dict[str, PlanMeal]:
    existing = db.query(PlanMeal).filter(PlanMeal.plan_id == plan.id).all()
    by_slot: dict[str, PlanMeal] = {}
    for pm in existing:
        by_slot[_normalize_slot(getattr(pm, "meal_type", ""))] = pm

    for slot in SLOTS:
        if slot in by_slot:
            continue
        pm = PlanMeal(
            plan_id=plan.id,
            meal_type=slot,
            order_index=SLOT_ORDER.get(slot, 0),
            title=slot.capitalize(),
            instructions="",
            recipe_id=None,
            kcal=0.0,
            protein_g=0.0,
            carbs_g=0.0,
            fat_g=0.0,
        )
        db.add(pm)
        db.flush()
        by_slot[slot] = pm

    return by_slot


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _apply_recipe_to_planmeal(pm: PlanMeal, rec: Recipe) -> None:
    pm.recipe_id = int(rec.id)
    pm.title = getattr(rec, "title", pm.title) or pm.title
    pm.instructions = getattr(rec, "instructions", pm.instructions) or (pm.instructions or "")
    # Set macros if present
    for f in ("kcal", "protein_g", "carbs_g", "fat_g"):
        if hasattr(pm, f) and hasattr(rec, f):
            setattr(pm, f, _safe_float(getattr(rec, f, 0.0), 0.0))

    # Replace heuristic placeholders with the selected recipe's real ingredients.
    pm.items.clear()
    raw_ingredients = getattr(rec, "ingredients", None) or []
    if isinstance(raw_ingredients, str):
        raw_ingredients = [line for line in raw_ingredients.splitlines() if line.strip()]
    for raw in raw_ingredients:
        name = ""
        qty = None
        unit = None
        if isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("ingredient") or raw.get("item") or "").strip()
            raw_qty = raw.get("qty", raw.get("quantity", raw.get("amount")))
            unit = raw.get("unit")
            try:
                qty = float(raw_qty) if raw_qty not in (None, "") else None
            except (TypeError, ValueError):
                # Keep free-form amounts visible instead of dropping them.
                unit = " ".join(str(v).strip() for v in (raw_qty, unit) if v not in (None, "")) or None
        else:
            name = str(raw).strip()
        if name:
            pm.items.append(
                PlanItem(
                    name=name,
                    qty=qty,
                    unit=unit,
                    meta={},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )


def _apply_ai_idea_to_planmeal(pm: PlanMeal, ai: dict[str, Any], created_recipe: Recipe) -> None:
    # Apply via the created recipe (so behavior matches "pick")
    _apply_recipe_to_planmeal(pm, created_recipe)


def _recompute_plan_totals_from_meals(by_slot: dict[str, PlanMeal]) -> dict[str, float]:
    tot = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for pm in by_slot.values():
        tot["kcal"] += _safe_float(getattr(pm, "kcal", 0.0), 0.0)
        tot["protein_g"] += _safe_float(getattr(pm, "protein_g", 0.0), 0.0)
        tot["carbs_g"] += _safe_float(getattr(pm, "carbs_g", 0.0), 0.0)
        tot["fat_g"] += _safe_float(getattr(pm, "fat_g", 0.0), 0.0)
    return tot


def _create_recipe_from_ai_idea(
    db: Session,
    slot: str,
    ai_idea: dict[str, Any],
    diet_tags: list[str] | None,
    primary_diet: str,
) -> Recipe:
    title = (ai_idea.get("title") or "AI-created meal").strip()
    approx = ai_idea.get("approx_macros") or {}
    ingredients = _coerce_ai_ingredients_to_storage(ai_idea.get("ingredients"))
    instructions = _coerce_ai_instructions_to_text(ai_idea.get("instructions"))
    protein_group = (ai_idea.get("protein_group") or "unknown").strip().lower()

    # Best-effort diet_tags storage
    tags: list[str] = []
    for t in diet_tags or []:
        t_norm = str(t).strip().lower()
        if t_norm and t_norm not in tags:
            tags.append(t_norm)
    if primary_diet and primary_diet not in ("omnivore", ""):
        if primary_diet not in tags:
            tags.append(primary_diet)

    r = Recipe(
        title=title,
        meal_type=slot,
        kcal=_safe_float(approx.get("kcal", 0.0), 0.0),
        protein_g=_safe_float(approx.get("protein_g", 0.0), 0.0),
        carbs_g=_safe_float(approx.get("carbs_g", 0.0), 0.0),
        fat_g=_safe_float(approx.get("fat_g", 0.0), 0.0),
        ingredients=ingredients,
        instructions=instructions,
        diet_tags=tags,
        protein_group=protein_group,
    )
    db.add(r)
    db.flush()
    return r


def _persist_day_recommendations(
    db: Session,
    user: User,
    day_iso: str,
    day_items: list[SlotRecommendation],
    day_diet_tags: list[str] | None,
    primary_diet: str,
) -> dict[str, Any]:
    try:
        day_date = datetime.fromisoformat(day_iso).date()
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date: {day_iso}")

    plan = _get_or_create_plan(db, user.id, day_date)

    # Respect locks: do not overwrite locked plans
    if getattr(plan, "locked", False):
        logger.info("LLM weekly persist: date=%s plan is locked; skipping persist", day_iso)
        return {"date": day_iso, "skipped": True, "reason": "locked", "applied": 0, "created_recipes": 0}

    by_slot = _ensure_plan_meals(db, plan)

    applied = 0
    created = 0

    # Only apply to canonical slots present in recommendations
    for it in day_items:
        slot = _normalize_slot(it.slot)
        if slot not in by_slot:
            continue

        pm = by_slot[slot]
        pm.meta = {**(pm.meta or {}), "reason": it.reason} if it.reason else (pm.meta or {})

        # Catalog pick
        if it.recipe and getattr(it.recipe, "id", None):
            rec = db.query(Recipe).filter(Recipe.id == int(it.recipe.id)).first()
            if rec:
                _apply_recipe_to_planmeal(pm, rec)
                applied += 1
            continue

        # AI-created
        ai = it.ai_idea or (it.meta or {}).get("ai_idea")
        if isinstance(ai, dict):
            created_recipe = _create_recipe_from_ai_idea(db, slot, ai, day_diet_tags, primary_diet)
            created += 1
            _apply_ai_idea_to_planmeal(pm, ai, created_recipe)
            applied += 1
            continue

    # Flip plan source
    try:
        plan.source = "llm"
    except Exception:
        pass

    # Recompute totals best-effort
    totals = _recompute_plan_totals_from_meals(by_slot)
    try:
        # Some schemas have Plan.kcal/protein fields; others store JSON totals.
        if hasattr(plan, "totals"):
            plan.totals = totals  # type: ignore[attr-defined]
        else:
            for f in ("kcal", "protein_g", "carbs_g", "fat_g"):
                if hasattr(plan, f) and f in totals:
                    setattr(plan, f, totals[f])
    except Exception:
        pass

    return {"date": day_iso, "skipped": False, "applied": applied, "created_recipes": created}


# ===========================
# Endpoints
# ===========================


@router.get("/health", tags=["llm"])
def llm_health(user: User = Depends(get_current_user)):
    _BUDGET.reset_if_new_day()
    return {
        "status": "degraded" if _circuit_open() else "ok",
        "provider_configured": bool(_get_openai_client()),
        "circuit_open": _circuit_open(),
    }


@router.post("/recommend", response_model=RecommendResponse, tags=["llm"])
def recommend_recipes(
    request: Request,
    payload: RecommendRequest = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.meals:
        raise HTTPException(status_code=400, detail="No meal targets provided")

    ip = request.client.host if request and request.client else "unknown"
    _RATE.check_and_add(user.id, ip)

    pref = _get_user_pref(db, user.id)
    pref_tags = _diet_tags_from_preferences(pref)
    primary_diet = _primary_diet_from_preferences(pref)
    req_tags = payload.diet_tags or []
    dedup: list[str] = []
    for t in pref_tags + req_tags:
        t_norm = t.strip().lower()
        if t_norm and t_norm not in dedup:
            dedup.append(t_norm)
    diet_tags = dedup or None

    target_date = _parse_iso_date(payload.date)
    week_used_recipe_ids = _get_week_used_recipe_ids(db, user, target_date)
    week_used_meal_keys = _get_week_used_meal_keys(db, user, target_date)
    week_protein_counts = _get_week_protein_counts(db, user, target_date)
    nutrition = calculate_training_nutrition(
        db=db,
        user=user,
        plan_date=target_date,
        baseline=_baseline_from_meals(payload.meals),
    )
    adjusted_meals = _apply_nutrition_targets(payload.meals, nutrition)

    # Weekly variety is mutable state. Include it in the cache key so an older
    # recommendation cannot be replayed after another day has used that meal.
    key = _cache_key(
        user.id,
        payload,
        pref_tags,
        week_used_meal_keys,
        nutrition.cache_fingerprint(),
    )
    cached = _CACHE.get(key)
    if cached:
        cached_items = cached.get("items") or []
        if cached_items and all(item.get("recipe") or item.get("ai_idea") for item in cached_items):
            return cached
        _CACHE.data.pop(key, None)
        logger.warning("LLM cache: discarded incomplete recommendation key=%s", key)

    client = _get_openai_client()
    provider = "openai" if client else "stub"
    allow_new = _allow_new_recipe()

    used_recipe_ids: set[int] = set(week_used_recipe_ids)
    used_meal_keys: set[str] = set(week_used_meal_keys)

    items: list[SlotRecommendation] = []
    used_protein_items: list[str] = []
    used_carb_items: list[str] = []

    for tgt in adjusted_meals:
        rec = _recommend_for_single_meal(
            client=client,
            db=db,
            date=payload.date,
            tgt=tgt,
            diet_tags=diet_tags,
            primary_diet=primary_diet,
            pref=pref,
            provider=provider,
            used_protein_items=used_protein_items,
            used_carb_items=used_carb_items,
            used_recipe_ids=used_recipe_ids,
            used_meal_keys=used_meal_keys,
            allow_new_recipe=allow_new,
            week_protein_counts=week_protein_counts,
            protein_cap_per_slot=2,
        )
        items.append(rec)

    missing_slots = _missing_recommendation_slots(items)
    if missing_slots:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Unable to generate a complete meal plan without weekly duplicates",
                "missing_slots": missing_slots,
            },
        )

    resp = RecommendResponse(
        provider=provider,
        items=items,
        nutrition=nutrition.to_dict(),
    ).model_dump()
    _CACHE.set(key, resp)
    return resp


# ---------- Weekly, training-aware API (persists) ----------


def _weekly_batch_schema() -> dict[str, Any]:
    ingredient = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "amount", "unit"],
        "properties": {
            "name": {"type": "string"},
            "amount": {"type": "string"},
            "unit": {"type": "string"},
        },
    }
    macros = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kcal", "protein_g", "carbs_g", "fat_g"],
        "properties": {name: {"type": "number"} for name in _MACROS},
    }
    meal = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "slot",
            "title",
            "ingredients",
            "instructions",
            "protein_group",
            "protein_item",
            "carb_item",
            "macros",
            "reason",
        ],
        "properties": {
            "slot": {"type": "string", "enum": list(SLOTS)},
            "title": {"type": "string"},
            "ingredients": {"type": "array", "minItems": 4, "maxItems": 8, "items": ingredient},
            "instructions": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "string"}},
            "protein_group": {
                "type": "string",
                "enum": ["fish", "poultry", "beef", "pork", "eggs", "dairy", "plant", "unknown"],
            },
            "protein_item": {"type": "string"},
            "carb_item": {"type": "string"},
            "macros": macros,
            "reason": {"type": "string"},
        },
    }
    return {
        "name": "glycofy_week_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["days"],
            "properties": {
                "days": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 14,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["date", "meals"],
                        "properties": {
                            "date": {"type": "string"},
                            "meals": {"type": "array", "minItems": 4, "maxItems": 4, "items": meal},
                        },
                    },
                }
            },
        },
    }


def _batch_week_recommendations(
    client: ClientType,
    *,
    days: list[dict[str, Any]],
    primary_diet: str,
    diet_tags: list[str],
    exclusions: list[str],
) -> tuple[dict[str, dict[str, SlotRecommendation]], dict[str, Any]]:
    """Generate the whole week in one model round-trip and discard unsafe cells."""
    if not client or not days or _circuit_open() or _BUDGET.spent_usd >= _daily_budget_usd():
        return {}, {"mode": "unavailable"}

    system = (
        "You are Glycofy's elite sports-nutrition planner. Design the COMPLETE week as one coherent plan. "
        "Return exactly one breakfast, lunch, dinner, and snack for every requested date. "
        "Respect diet tags and ingredient exclusions as hard safety constraints. Keep every recipe practical, "
        "single-serving, and cookable in about 30 minutes with measured ingredients. Keep calories, protein, "
        "carbohydrates, and fat within 15% of each slot target; verify that the four macro values are internally "
        "plausible before responding. A day's training object can describe upcoming training. On those days, favor "
        "digestible carbohydrate before the workout and carbohydrate plus protein afterward, using next_workout_at "
        "for timing; mention the workout in the reason without making medical claims. Never repeat a meal title "
        "during the week. Within each day, do not "
        "repeat a protein_item or carb_item across breakfast, lunch, and dinner. Across adjacent days, vary main "
        "proteins. Use no protein_group more than twice for the same slot during the week when alternatives exist. "
        "Plan globally first so variety is intentional, then emit only the requested structured data."
    )
    payload = {
        "primary_diet": primary_diet,
        "diet_tags": diet_tags,
        "ingredient_exclusions": exclusions,
        "days": days,
    }
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=_openai_model(),
            temperature=0.2,
            max_tokens=int(os.environ.get("OPENAI_WEEKLY_MAX_TOKENS", "12000")),
            response_format={"type": "json_schema", "json_schema": _weekly_batch_schema()},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        usage, cost = _extract_usage_meta(response)
        _record_success(cost)
        raw = response.choices[0].message.content if response.choices else None
        parsed = json.loads(raw) if raw else {}
    except Exception as exc:
        _record_failure()
        logger.exception("LLM weekly batch failed: %s", exc)
        return {}, {"mode": "error", "error": type(exc).__name__}

    targets = {(day["date"], meal["slot"]): meal["target_macros"] for day in days for meal in day.get("meals", [])}
    valid_dates = {day["date"] for day in days}
    output: dict[str, dict[str, SlotRecommendation]] = {}
    week_titles: set[str] = set()
    rejected = 0

    for day in parsed.get("days", []):
        date_iso = str(day.get("date") or "")
        if date_iso not in valid_dates:
            continue
        slots: dict[str, SlotRecommendation] = {}
        day_proteins: set[str] = set()
        day_carbs: set[str] = set()
        for meal in day.get("meals", []):
            slot = _normalize_slot(str(meal.get("slot") or ""))
            target = targets.get((date_iso, slot))
            title = str(meal.get("title") or "").strip()
            protein = str(meal.get("protein_item") or "").strip().lower()
            carb = str(meal.get("carb_item") or "").strip().lower()
            ingredients = meal.get("ingredients") or []
            instructions = meal.get("instructions") or []
            macros = meal.get("macros") or {}
            protein_group = str(meal.get("protein_group") or "unknown").strip().lower()
            title_key = _meal_similarity_key(title)
            macro_ok = bool(target) and all(
                _safe_float(target.get(name)) <= 0
                or abs(_safe_float(macros.get(name)) - _safe_float(target.get(name))) / _safe_float(target.get(name))
                <= 0.25
                for name in _MACROS
            )
            invalid = (
                slot not in SLOTS
                or slot in slots
                or not target
                or not title_key
                or title_key in week_titles
                or not _ingredients_have_quantities(ingredients)
                or not isinstance(instructions, list)
                or len(instructions) < 2
                or not macro_ok
                or _text_violates_exclusions(f"{title} {json.dumps(ingredients)}", exclusions)
            )
            if slot in _DAY_UNIQUE_SLOTS:
                invalid = invalid or not protein or not carb or protein in day_proteins or carb in day_carbs
            if invalid:
                rejected += 1
                continue
            ai_idea = {
                "title": title,
                "ingredients": ingredients,
                "instructions": instructions,
                "protein_group": protein_group,
                "protein_item": protein,
                "carb_item": carb,
                "approx_macros": {
                    name: _safe_float(macros.get(name), _safe_float(target.get(name))) for name in _MACROS
                },
            }
            slots[slot] = SlotRecommendation(
                slot=slot,
                target={name: _safe_float(target.get(name)) for name in _MACROS},
                reason=str(meal.get("reason") or "Balanced for your weekly goals."),
                meta={
                    "provider": "openai",
                    "mode": "create",
                    "batch": True,
                    "protein_group": ai_idea["protein_group"],
                    "protein_item": protein,
                    "carb_item": carb,
                    "ai_idea": ai_idea,
                },
                ai_idea=ai_idea,
            )
            week_titles.add(title_key)
            if slot in _DAY_UNIQUE_SLOTS:
                day_proteins.add(protein)
                day_carbs.add(carb)
        output[date_iso] = slots

    meta = {
        "mode": "batch",
        "latency_ms": latency_ms,
        "usage": usage,
        "cost_usd": round(cost, 6),
        "accepted": sum(len(slots) for slots in output.values()),
        "rejected": rejected,
    }
    logger.info("LLM weekly batch completed: %s", meta)
    return output, meta


@router.post("/recommend/weekly/apply_payload", tags=["llm"])
def recommend_weekly_apply(
    request: Request,
    payload: WeeklyRecommendRequest = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.days:
        raise HTTPException(status_code=400, detail="No days provided")

    ip = request.client.host if request and request.client else "unknown"
    _RATE.check_and_add(user.id, ip)

    dates = [d.date for d in payload.days if d.date]
    logger.info("LLM weekly: user_id=%s ip=%s days=%s", user.id, ip, dates)

    pref = _get_user_pref(db, user.id)
    pref_tags = _diet_tags_from_preferences(pref)
    primary_diet = _primary_diet_from_preferences(pref)

    client = _get_openai_client()
    provider = "openai" if client else "stub"
    allow_new = _allow_new_recipe()

    global_dinner_base: MealTarget | None = None
    for d in payload.days:
        for m in d.meals:
            if m.slot == "dinner":
                global_dinner_base = m
                break
        if global_dinner_base is not None:
            break

    out_days: list[dict[str, Any]] = []
    used_recipe_ids: set[int] = set()
    used_meal_keys: set[str] = set()
    week_protein_counts: dict[tuple[str, str], int] = {}

    persist_summaries: list[dict[str, Any]] = []

    # Cross-day rolling diversity tracking
    rolling_protein_items: list[str] = []
    rolling_carb_items: list[str] = []

    # Build all training-adjusted targets up front and ask the model to design
    # the week globally. Missing/invalid cells fall through to the proven
    # per-slot recommender below as targeted repairs.
    batch_days: list[dict[str, Any]] = []
    for requested_day in payload.days:
        if not requested_day.meals:
            continue
        balanced_meals = _balanced_weekly_targets(requested_day)
        requested_date = _parse_iso_date(requested_day.date)
        requested_nutrition = calculate_training_nutrition(
            db=db,
            user=user,
            plan_date=requested_date,
            baseline=_baseline_from_meals(balanced_meals),
        )
        requested_targets = _apply_nutrition_targets(balanced_meals, requested_nutrition)
        batch_days.append(
            {
                "date": requested_day.date,
                "training": requested_nutrition.to_dict()["training"],
                "diet_tags": requested_day.diet_tags or [],
                "meals": [
                    {"slot": meal.slot, "target_macros": meal.model_dump(exclude={"slot"})}
                    for meal in requested_targets
                ],
            }
        )

    batch_items, batch_meta = _batch_week_recommendations(
        client,
        days=batch_days,
        primary_diet=primary_diet,
        diet_tags=pref_tags,
        exclusions=_preference_exclusions(pref),
    )

    for day in payload.days:
        date_iso = day.date
        if not day.meals:
            out_days.append(
                {
                    "date": date_iso,
                    "factor": 1.0,
                    "training": {
                        "factor": 1.0,
                        "metric_name": "score",
                        "metric_value": 0.0,
                        "score": 0.0,
                        "is_race": False,
                        "zone": "steady",
                    },
                    "items": [],
                }
            )
            persist_summaries.append({"date": date_iso, "skipped": True, "reason": "no_meals", "applied": 0})
            continue

        req_tags = day.diet_tags or []
        dedup: list[str] = []
        for t in pref_tags + req_tags:
            t_norm = t.strip().lower()
            if t_norm and t_norm not in dedup:
                dedup.append(t_norm)
        day_diet_tags = dedup or None

        plan_date = _parse_iso_date(date_iso)
        balanced_meals = _balanced_weekly_targets(day)
        nutrition = calculate_training_nutrition(
            db=db,
            user=user,
            plan_date=plan_date,
            baseline=_baseline_from_meals(balanced_meals),
        )
        adjusted_meals = _apply_nutrition_targets(balanced_meals, nutrition)
        factor = nutrition.final.kcal / nutrition.baseline.kcal if nutrition.baseline.kcal > 0 else 1.0

        # Carry forward recent protein/carb history across days
        used_protein_items: list[str] = list(dict.fromkeys(rolling_protein_items[-8:]))

        used_carb_items: list[str] = list(dict.fromkeys(rolling_carb_items[-8:]))
        prior_protein_count = len(used_protein_items)
        prior_carb_count = len(used_carb_items)

        day_items: list[SlotRecommendation] = []

        for scaled in adjusted_meals:
            slot = _normalize_slot(scaled.slot)
            rec = batch_items.get(date_iso, {}).get(slot)
            if rec is None:
                rec = _recommend_for_single_meal(
                    client=client,
                    db=db,
                    date=date_iso,
                    tgt=scaled,
                    diet_tags=day_diet_tags,
                    primary_diet=primary_diet,
                    pref=pref,
                    provider=provider,
                    used_protein_items=used_protein_items,
                    used_carb_items=used_carb_items,
                    used_recipe_ids=used_recipe_ids,
                    used_meal_keys=used_meal_keys,
                    allow_new_recipe=allow_new,
                    week_protein_counts=week_protein_counts,
                    protein_cap_per_slot=2,
                    prefer_fast_catalog=True,
                )
            else:
                meta = rec.meta or {}
                protein_item = str(meta.get("protein_item") or "").strip().lower()
                carb_item = str(meta.get("carb_item") or "").strip().lower()
                protein_group = str(meta.get("protein_group") or "").strip().lower()
                meal_key = _meal_similarity_key((rec.ai_idea or {}).get("title"))
                if meal_key:
                    used_meal_keys.add(meal_key)
                if slot in _DAY_UNIQUE_SLOTS:
                    if protein_item:
                        used_protein_items.append(protein_item)
                    if carb_item:
                        used_carb_items.append(carb_item)
                if protein_group and protein_group != "unknown":
                    key = (slot, protein_group)
                    week_protein_counts[key] = week_protein_counts.get(key, 0) + 1
            day_items.append(rec)

        slots_present = sorted({it.slot for it in day_items})
        if "dinner" not in slots_present and global_dinner_base is not None:
            fallback_dinner = _apply_nutrition_targets(
                [global_dinner_base],
                TrainingNutritionResult(
                    baseline=_baseline_from_meals([global_dinner_base]),
                    training=nutrition.training,
                    adjustment=nutrition.adjustment,
                    final=MacroTargets(
                        kcal=global_dinner_base.kcal * factor,
                        protein_g=global_dinner_base.protein_g,
                        carbs_g=global_dinner_base.carbs_g
                        * (
                            nutrition.final.carbs_g / nutrition.baseline.carbs_g
                            if nutrition.baseline.carbs_g > 0
                            else 1.0
                        ),
                        fat_g=global_dinner_base.fat_g,
                    ),
                    rationale=nutrition.rationale,
                ),
            )[0]

            dinner_rec = _recommend_for_single_meal(
                client=client,
                db=db,
                date=date_iso,
                tgt=fallback_dinner,
                diet_tags=day_diet_tags,
                primary_diet=primary_diet,
                pref=pref,
                provider=provider,
                used_protein_items=used_protein_items,
                used_carb_items=used_carb_items,
                used_recipe_ids=used_recipe_ids,
                used_meal_keys=used_meal_keys,
                allow_new_recipe=allow_new,
                week_protein_counts=week_protein_counts,
                protein_cap_per_slot=2,
                prefer_fast_catalog=True,
            )
            day_items.append(dinner_rec)

        missing_slots = _missing_recommendation_slots(day_items)
        if missing_slots:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Unable to generate a complete weekly meal plan without duplicates",
                    "date": date_iso,
                    "missing_slots": missing_slots,
                },
            )

        out_days.append(
            {
                "date": date_iso,
                "factor": factor,
                "training": nutrition.to_dict(),
                "items": [it.model_dump() for it in day_items],
            }
        )

        # Carry this day's selected protein/carb identities into the next day.
        # This makes the rolling history real rather than resetting it for
        # every day, preventing adjacent-day repetition in main meals.
        rolling_protein_items = list(dict.fromkeys(used_protein_items[prior_protein_count:]))
        rolling_carb_items = list(dict.fromkeys(used_carb_items[prior_carb_count:]))

        # ✅ Persist this day into plans/plan_meals
        persist_summaries.append(
            _persist_day_recommendations(
                db=db,
                user=user,
                day_iso=date_iso,
                day_items=day_items,
                day_diet_tags=day_diet_tags,
                primary_diet=primary_diet,
            )
        )

    # ✅ Commit once at the end so changes persist
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("LLM weekly: failed to commit persisted plans: %s", e)
        raise HTTPException(status_code=500, detail="Failed to persist weekly recommendations")

    resp = {
        "provider": provider,
        "days": out_days,
        "generation": batch_meta,
        "persist": {
            "applied": persist_summaries,
        },
    }
    logger.info("LLM weekly: user_id=%s completed provider=%s days=%d", user.id, provider, len(out_days))
    return resp


_WEEKLY_JOBS: dict[str, dict[str, Any]] = {}
_WEEKLY_JOBS_LOCK = threading.Lock()
_WEEKLY_JOB_TTL_SECONDS = 60 * 60
_WEEKLY_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="weekly-plan")


def _update_weekly_job(job_id: str, **updates: Any) -> None:
    with _WEEKLY_JOBS_LOCK:
        if job_id in _WEEKLY_JOBS:
            _WEEKLY_JOBS[job_id].update(updates)


def _run_weekly_job(job_id: str, payload_data: dict[str, Any], user_id: int, ip: str) -> None:
    started = time.perf_counter()
    _update_weekly_job(
        job_id,
        status="running",
        stage="generating",
        message="Designing 28 meals with AI…",
        started_monotonic=started,
    )
    with SessionLocal() as db:
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user is None:
                raise RuntimeError("User no longer exists")

            class JobRequest:
                pass

            job_request = JobRequest()
            job_request.client = type("JobClient", (), {"host": ip})()

            result = recommend_weekly_apply(
                job_request,  # type: ignore[arg-type]
                WeeklyRecommendRequest.model_validate(payload_data),
                db,
                user,
            )
            elapsed = round(time.perf_counter() - started, 2)
            _update_weekly_job(
                job_id,
                status="completed",
                stage="completed",
                message="Your AI week is ready.",
                completed_days=len(result.get("days", [])),
                elapsed_seconds=elapsed,
                result=result,
                completed_at=time.time(),
            )
        except Exception as exc:
            db.rollback()
            logger.exception("LLM weekly job failed job_id=%s", job_id)
            _update_weekly_job(
                job_id,
                status="failed",
                stage="failed",
                message="We couldn't finish this week plan.",
                error=str(getattr(exc, "detail", None) or exc),
                elapsed_seconds=round(time.perf_counter() - started, 2),
                completed_at=time.time(),
            )


def _prune_weekly_jobs() -> None:
    cutoff = time.time() - _WEEKLY_JOB_TTL_SECONDS
    with _WEEKLY_JOBS_LOCK:
        expired = [
            job_id
            for job_id, job in _WEEKLY_JOBS.items()
            if job.get("completed_at", job.get("created_at", time.time())) < cutoff
        ]
        for job_id in expired:
            _WEEKLY_JOBS.pop(job_id, None)


@router.post("/recommend/weekly/jobs", response_model=WeeklyJobStartResponse, tags=["llm"])
def start_weekly_job(
    request: Request,
    payload: WeeklyRecommendRequest = Body(...),
    user: User = Depends(get_current_user),
):
    if not payload.days:
        raise HTTPException(status_code=400, detail="No days provided")
    _prune_weekly_jobs()
    ip = request.client.host if request and request.client else "unknown"
    with _WEEKLY_JOBS_LOCK:
        existing = next(
            (
                job
                for job in _WEEKLY_JOBS.values()
                if job.get("user_id") == user.id and job.get("status") in {"queued", "running"}
            ),
            None,
        )
        if existing:
            return {"job_id": existing["job_id"], "status": existing["status"]}
        job_id = uuid.uuid4().hex
        _WEEKLY_JOBS[job_id] = {
            "job_id": job_id,
            "user_id": user.id,
            "status": "queued",
            "stage": "queued",
            "message": "Starting your AI week…",
            "completed_days": 0,
            "total_days": len(payload.days),
            "elapsed_seconds": 0.0,
            "result": None,
            "error": None,
            "created_at": time.time(),
        }
    _WEEKLY_JOB_EXECUTOR.submit(_run_weekly_job, job_id, payload.model_dump(), user.id, ip)
    return {"job_id": job_id, "status": "queued"}


@router.get("/recommend/weekly/jobs/{job_id}", response_model=WeeklyJobStatusResponse, tags=["llm"])
def weekly_job_status(job_id: str, user: User = Depends(get_current_user)):
    with _WEEKLY_JOBS_LOCK:
        job = dict(_WEEKLY_JOBS.get(job_id) or {})
    if not job or job.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Weekly plan job not found")
    if job.get("status") in {"queued", "running"}:
        started = job.get("started_monotonic")
        elapsed = round(time.perf_counter() - started, 1) if started else 0.0
        job["elapsed_seconds"] = elapsed
        phases = (
            (45, "saving", "Saving your personalized week…"),
            (32, "instructions", "Adding quantities and cooking instructions…"),
            (22, "safety", "Checking diet and ingredient exclusions…"),
            (12, "balancing", "Balancing macros and weekly variety…"),
            (0, "generating", "Designing 28 meals as one balanced week…"),
        )
        for threshold, stage, message in phases:
            if elapsed >= threshold:
                job.update({"stage": stage, "message": message})
                break
    return WeeklyJobStatusResponse.model_validate(job)


# ---------- Training curve API (for graphs in UI) ----------


@router.get("/recommend/training_curve", tags=["llm"])
def training_curve(
    days: int = Query(28, ge=1, le=90, description="Number of recent days to include (default 28)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = datetime.now(UTC).date()
    dates: list[str] = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        dates.append(d.strftime("%Y-%m-%d"))

    training_meta = _compute_training_factors_for_week(db, user.id, dates)
    series: list[dict[str, Any]] = []

    metric_counts: dict[str, int] = {}
    for d in dates:
        m = training_meta.get(d)
        if not m:
            continue
        name = m.get("metric_name", "score")
        metric_counts[name] = metric_counts.get(name, 0) + 1
    metric_name = max(metric_counts.items(), key=lambda kv: kv[1])[0] if metric_counts else "score"

    for d in dates:
        m = training_meta.get(
            d,
            {
                "factor": 1.0,
                "metric_name": metric_name,
                "metric_value": 0.0,
                "score": 0.0,
                "is_race": False,
                "zone": "steady",
            },
        )
        series.append(
            {
                "date": d,
                "metric_value": float(m.get("metric_value", 0.0)),
                "score": float(m.get("score", 0.0)),
                "factor": float(m.get("factor", 1.0)),
                "zone": str(m.get("zone", "steady")),
                "is_race": bool(m.get("is_race", False)),
            }
        )

    return {"metric_name": metric_name, "days": series}
