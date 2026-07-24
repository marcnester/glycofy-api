# app/services/mealgen.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Recipe,  # your canonical recipes model
    User,
)
from app.routers.plan_models import EnergyTarget, UserPreference


@dataclass
class DailyTargets:
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


_MEAL_SPLIT = {
    "breakfast": 0.25,
    "lunch": 0.30,
    "dinner": 0.30,
    "snack": 0.15,
}


def _default_meal_order(meal_type: str) -> int:
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
    return order.get(meal_type, 99)


def _safe_meal_type(s: str) -> str:
    s = (s or "").strip().lower()
    return s if s in {"breakfast", "lunch", "dinner", "snack"} else "snack"


def _estimate_targets(db: Session, user: User, day: date) -> DailyTargets:
    # Prefer explicit EnergyTarget if present
    et = db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
    if et and (et.target_kcal or et.tdee_kcal):
        kcal = float(et.target_kcal or et.tdee_kcal or 2400.0)
    else:
        kcal = 2400.0  # dev default; replace with TDEE calc later

    # Slight athlete bias
    protein = max(120.0, round(kcal * 0.30 / 4))
    carbs = round(kcal * 0.45 / 4)
    fat = round(kcal * 0.25 / 9)
    return DailyTargets(kcal=kcal, protein_g=protein, carbs_g=carbs, fat_g=fat)


def _score_recipe(targets: tuple[float, float, float, float], r: Recipe) -> float:
    """Lower is better. Weighted absolute deltas across kcal, P, C, F."""
    tk, tp, tc, tf = targets
    dk = abs((r.kcal or 0) - tk)
    dp = abs((r.protein_g or 0) - tp)
    dc = abs((r.carbs_g or 0) - tc)
    df = abs((r.fat_g or 0) - tf)
    # Emphasize kcal + protein
    return dk * 1.0 + dp * 2.0 + dc * 1.0 + df * 1.0


def _find_best_recipe(
    db: Session,
    meal_type: str,
    kcal: float,
    p: float,
    c: float,
    f: float,
    pref: UserPreference | None = None,
) -> Recipe | None:
    q = db.query(Recipe).filter(Recipe.meal_type == meal_type)
    # Later: filter diet tags, allergies, bans
    candidates = q.limit(250).all()
    if not candidates:
        return None
    tgt = (kcal, p, c, f)
    best = min(candidates, key=lambda r: _score_recipe(tgt, r))
    return best


def _recipe_to_plan_meal(r: Recipe, meal_type: str, order_index: int) -> dict[str, Any]:
    return {
        "meal_type": meal_type,
        "title": r.title,
        "kcal": r.kcal,
        "protein_g": r.protein_g,
        "carbs_g": r.carbs_g,
        "fat_g": r.fat_g,
        "instructions": r.instructions,
        "tags": [t.strip() for t in (r.diet_tags or "").split(",") if t.strip()],
        "order_index": order_index,
        # ingredients in DB are stored as text (newline-separated) — normalize to items
        "ingredients": [
            {
                "name": line.strip(),
                "qty": None,
                "unit": None,
                "kcal": None,
                "protein_g": None,
                "carbs_g": None,
                "fat_g": None,
                "meta": {},
            }
            for line in (r.ingredients or "").splitlines()
            if line.strip()
        ],
    }


def _synthetic_meal(meal_type: str, kcal: float, p: float, c: float, f: float) -> dict[str, Any]:
    return {
        "meal_type": meal_type,
        "title": meal_type.title(),
        "kcal": round(kcal),
        "protein_g": round(p),
        "carbs_g": round(c),
        "fat_g": round(f),
        "instructions": None,
        "tags": [],
        "order_index": _default_meal_order(meal_type),
        "ingredients": [
            {
                "name": "Protein (lean)",
                "qty": 1,
                "unit": "serv",
                "kcal": None,
                "protein_g": p * 0.7,
                "carbs_g": None,
                "fat_g": None,
                "meta": {},
            },
            {
                "name": "Complex carbs",
                "qty": 1,
                "unit": "serv",
                "kcal": None,
                "protein_g": None,
                "carbs_g": c * 0.7,
                "fat_g": None,
                "meta": {},
            },
            {
                "name": "Fats (healthy)",
                "qty": 1,
                "unit": "serv",
                "kcal": None,
                "protein_g": None,
                "carbs_g": None,
                "fat_g": f * 0.7,
                "meta": {},
            },
        ],
    }


def _aggregate_totals(meals: list[dict[str, Any]]) -> dict[str, float]:
    t = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for m in meals:
        t["kcal"] += float(m.get("kcal") or 0)
        t["protein_g"] += float(m.get("protein_g") or 0)
        t["carbs_g"] += float(m.get("carbs_g") or 0)
        t["fat_g"] += float(m.get("fat_g") or 0)
    return {k: (round(v) if k == "kcal" else round(v)) for k, v in t.items()}


def generate_plan_seed_heuristic(db: Session, user: User, day: date) -> dict[str, Any]:
    targets = _estimate_targets(db, user, day)
    seed_meals: list[dict[str, Any]] = []
    # try recipe first per slot; fallback to synthetic if none
    for mt, frac in _MEAL_SPLIT.items():
        kcal = targets.kcal * frac
        p = targets.protein_g * frac
        c = targets.carbs_g * frac
        f = targets.fat_g * frac

        r = _find_best_recipe(db, mt, kcal, p, c, f, None)
        if r:
            seed_meals.append(_recipe_to_plan_meal(r, mt, _default_meal_order(mt)))
        else:
            seed_meals.append(_synthetic_meal(mt, kcal, p, c, f))

    return {
        "totals": _aggregate_totals(seed_meals),
        "meals": seed_meals,
        "source": "heuristic",
    }


async def generate_plan_seed_llm(
    db: Session,
    user: User,
    day: date,
    preferences: UserPreference | None,
    targets: EnergyTarget | None,
) -> dict[str, Any]:
    """
    Placeholder LLM seam.
    Later: build prompt with (targets, preferences, pantry, disliked ingredients),
    call provider, validate schema (kcal/P/C/F per meal), map to our PlanMeal/PlanItem structure.
    """
    # For now, just call heuristic so the route is functional.
    return generate_plan_seed_heuristic(db, user, day)
