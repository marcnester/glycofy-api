"""Adjust saved meal portions when training inputs change."""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from math import isfinite

from sqlalchemy.orm import Session

from app.models import Plan, PlanMeal, User
from app.services.training_nutrition import (
    MACROS,
    TrainingNutritionResult,
    WorkoutInput,
    allocate_meal_targets,
    athlete_profile_baseline,
    calculate_heuristic_nutrition,
    calculate_training_nutrition,
)
from app.services.usda_nutrition import fit_portions_to_targets
from app.services.workout_reconciliation import user_timezone


def lock_training_changes(db: Session, user: User) -> None:
    db.query(User.id).filter(User.id == user.id).with_for_update(key_share=True).one()


def _macros(row: PlanMeal) -> dict[str, float]:
    return {key: float(getattr(row, key) or 0) for key in MACROS}


def _meal_portions(meal: PlanMeal) -> dict:
    return {
        "macros": {key: getattr(meal, key) for key in MACROS},
        "items": {str(item.id): {key: getattr(item, key) for key in ("qty", *MACROS)} for item in meal.items},
    }


def _restore_portions(meal: PlanMeal, portions: dict) -> None:
    for key, value in portions["macros"].items():
        setattr(meal, key, value)
    for item in meal.items:
        for key, value in portions["items"][str(item.id)].items():
            setattr(item, key, value)
        item.updated_at = datetime.utcnow()


_FIXED_QUANTITY = re.compile(r"\d\s*(?:g|grams?|kg|mg|ml|liters?|oz|lb|cups?|tbsp|tsp)\b", re.I)


def _training_weights(plan: Plan) -> tuple[dict, dict]:
    origins = {}
    weights = {}
    for meal in plan.meals:
        origin = (meal.meta or {}).get("training_replan_origin")
        if origin and origin["portions"]["items"].keys() == {str(item.id) for item in meal.items}:
            origins[meal.meal_type] = origin
            weights[meal.meal_type] = origin["weights"]
        else:
            weights[meal.meal_type] = _macros(meal)
    return origins, weights


def _target_from_origin(origin: dict, prescription: dict[str, float]) -> dict[str, float]:
    return {
        key: round(max(0.0, origin["target"][key] + prescription[key] - origin["prescription"][key]), 1)
        for key in MACROS
    }


def fueling_adjustment_targets(plan: Plan, nutrition: TrainingNutritionResult) -> dict:
    origins, weights = _training_weights(plan)
    return {
        slot: _target_from_origin(origins[slot], {key: round(value, 6) for key, value in target.items()})
        for slot, target in allocate_meal_targets(nutrition, weights).items()
        if slot in origins
    }


def fueling_adjustment_preview(meal: PlanMeal, current_target: dict | None) -> dict | None:
    """Choose one measured carb portion already on the meal's grocery list."""
    meta = meal.meta or {}
    origin = meta.get("training_replan_origin")
    target = meta.get("training_replan_target")
    if (
        meta.get("training_replan") != "review"
        or not origin
        or not target
        or target != current_target
        or (meal.feedback and meal.feedback.outcome in {"eaten", "substituted", "skipped"})
        or _FIXED_QUANTITY.search(meal.instructions or "")
    ):
        return None
    portions = _meal_portions(meal)
    if portions["items"].keys() != origin["portions"]["items"].keys():
        return None
    if any(value is None or not isfinite(value) or value < 0 for value in portions["macros"].values()):
        return None
    current = _macros(meal)
    gap = target["carbs_g"] - current["carbs_g"]
    if abs(gap) < 1:
        return None

    best = None
    best_error = abs(gap)
    for item in meal.items:
        values = {key: getattr(item, key) for key in MACROS}
        if (
            item.unit not in {"g", "gram", "grams"}
            or not item.qty
            or not isfinite(item.qty)
            or item.qty < 1
            or (item.meta or {}).get("nutrition_basis") != "usda_fooddata_central"
            or any(value is None or not isfinite(value) or value < 0 for value in values.values())
            or values["carbs_g"] < 1
        ):
            continue
        ratio = max(0.5, min(2, 1 + gap / values["carbs_g"]))
        qty = max(1.0, round(item.qty * ratio, 1))
        scaled = {key: round(value * qty / item.qty, 1) for key, value in values.items()}
        totals = {key: round(current[key] + scaled[key] - values[key], 1) for key in MACROS}
        # Keep a carb correction from pushing calories or other macros far
        # beyond the current portion or planned target.
        if any(
            not max(0, min(current[key], target[key] - margin))
            <= totals[key]
            <= max(current[key], target[key]) + margin
            for key, margin in (("kcal", max(50, target["kcal"] * 0.15)), ("protein_g", 10), ("fat_g", 5))
        ):
            continue
        error = abs(totals["carbs_g"] - target["carbs_g"])
        change = round(totals["carbs_g"] - current["carbs_g"], 1)
        if error >= best_error or abs(change) < 1:
            continue
        best_error = error
        best = {
            "ingredient_id": item.id,
            "ingredient": item.name,
            "current_qty_g": item.qty,
            "proposed_qty_g": qty,
            "current_carbs_g": current["carbs_g"],
            "target_carbs_g": target["carbs_g"],
            "proposed_carbs_g": totals["carbs_g"],
            "change_carbs_g": change,
            "item_macros": scaled,
            "meal_macros": totals,
        }
    if best is None:
        return None
    # Bind approval to the exact current portions, target and recipe directions.
    payload = [meal.id, portions, origin, target, meal.instructions, best]
    best["token"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return best


def apply_fueling_adjustment(meal: PlanMeal, preview: dict) -> None:
    item = next(item for item in meal.items if item.id == preview["ingredient_id"])
    item.qty = preview["proposed_qty_g"]
    item.updated_at = datetime.utcnow()
    for key in MACROS:
        setattr(item, key, preview["item_macros"][key])
        setattr(meal, key, preview["meal_macros"][key])
    meta = meal.meta or {}
    meal.meta = {
        **meta,
        "training_replan": "adjusted",
        "training_carb_change": {
            "grams": preview["change_carbs_g"],
            "workouts": meta.get("training_replan_workouts", []),
        },
    }


def _update_meal(meal: PlanMeal, target: dict[str, float], *, heuristic: bool) -> str:
    original = _macros(meal)
    if heuristic and not meal.recipe_id and meal.title == meal.meal_type.title():
        for key, value in target.items():
            setattr(meal, key, round(value, 1))
        for item in meal.items:
            for key, value in original.items():
                if value and getattr(item, key) is not None:
                    setattr(item, key, round(getattr(item, key) * target[key] / value, 1))
        return "adjusted"

    # Older directions can hard-code ingredient amounts. Review those meals
    # rather than giving the cook two conflicting sets of quantities.
    if _FIXED_QUANTITY.search(meal.instructions or ""):
        return "review"

    ingredients = []
    for item in meal.items:
        nutrition = {key: getattr(item, key) for key in MACROS}
        if (
            item.unit not in {"g", "gram", "grams"}
            or not item.qty
            or (item.meta or {}).get("nutrition_basis") != "usda_fooddata_central"
            or any(value is None for value in nutrition.values())
        ):
            return "review"
        ingredients.append(
            {
                "name": item.name,
                "usda_search_query": (item.meta or {}).get("usda_search_query"),
                "amount_g": item.qty,
                "nutrition": nutrition,
            }
        )
    if not ingredients:
        return "review"

    fitted = fit_portions_to_targets(ingredients, target)
    totals = {key: round(sum(row["nutrition"][key] for row in fitted), 1) for key in MACROS}
    if any(abs(totals[key] - target[key]) > max(2, target[key] * 0.15) for key in MACROS):
        return "review"
    if fitted == ingredients or any(
        abs(totals[key] - target[key]) >= abs(original[key] - target[key])
        for key in ("carbs_g", "kcal")
        if abs(original[key] - target[key]) >= (5 if key == "carbs_g" else 20)
    ):
        return "review"

    for item, row in zip(meal.items, fitted, strict=True):
        item.qty = row["amount_g"]
        item.updated_at = datetime.utcnow()
        for key, value in row["nutrition"].items():
            setattr(item, key, value)
    for key, value in totals.items():
        setattr(meal, key, value)
    return "adjusted"


def _changed_workouts(before: TrainingNutritionResult, after: TrainingNutritionResult) -> list[WorkoutInput]:
    old = {(row.reason, row.workout_id): row for row in before.workout_inputs}
    new = {(row.reason, row.workout_id): row for row in after.workout_inputs}
    return [
        row
        for key, row in (old | new).items()
        if old.get(key) != new.get(key)
        and (row.reason != "before_early_workout" or before.dinner_carbs_g != after.dinner_carbs_g)
    ]


def _meal_workout_reasons(workouts: list[WorkoutInput], slot: str) -> list[dict[str, str | None]]:
    cross_day = {
        "before_early_workout": "dinner",
        "after_late_workout": "breakfast",
        "after_late_activity": "breakfast",
    }
    relevant = [row for row in workouts if cross_day.get(row.reason, slot) == slot]
    return [
        {"reason": row.reason, "sport": row.sport, "date": row.date, "start_time": row.start_time}
        for row in relevant or workouts
    ]


@contextmanager
def replan_training_changes(
    db: Session,
    user: User,
    dates: Iterable[date] | None = None,
) -> Iterator[dict[str, int]]:
    """Compare targets at a fixed time and update existing plans in the caller's transaction.

    Omitting dates scopes a completed-activity sync to the active days
    where recovery or a reconciled planned session can affect meals.
    """
    now = datetime.now(UTC)
    today = now.astimezone(user_timezone(user)).date()
    candidate_days = (
        {today + timedelta(days=offset) for offset in range(3)}
        if dates is None
        else {day + timedelta(days=offset) for day in dates for offset in (-1, 0, 1)}
    )
    summary = {"adjusted_meals": 0, "meals_to_review": 0}
    candidate_days = {day for day in candidate_days if day >= today}
    if not candidate_days:
        yield summary
        return

    # Serialize changes to the same plan with its lock endpoint and competing
    # workout requests. Refresh the row in case it was loaded before a lock.
    plans = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.date.in_(candidate_days), Plan.locked.is_(False))
        .order_by(Plan.date)
        .populate_existing()
        .with_for_update()
        .all()
    )

    def nutrition(plan: Plan) -> TrainingNutritionResult:
        if plan.source == "heuristic":
            return calculate_heuristic_nutrition(db=db, user=user, plan_date=plan.date, now=now)
        return calculate_training_nutrition(
            db=db, user=user, plan_date=plan.date, baseline=athlete_profile_baseline(user, plan.date), now=now
        )

    before = {plan.date: nutrition(plan) for plan in plans}
    yield summary
    db.flush()
    for plan in plans:
        after = nutrition(plan)
        workouts = _changed_workouts(before[plan.date], after)
        origins, weights = _training_weights(plan)
        old_targets = allocate_meal_targets(before[plan.date], weights)
        new_targets = allocate_meal_targets(after, weights)
        changed = False
        for meal in plan.meals:
            if meal.feedback and meal.feedback.outcome in {"eaten", "substituted", "skipped"}:
                continue
            slot = meal.meal_type
            origin = origins.get(slot)
            prescription = {key: round(value, 6) for key, value in new_targets[slot].items()}
            delta = {key: new_targets[slot][key] - old_targets[slot][key] for key in MACROS}
            restoring = origin is not None and prescription == origin["prescription"]
            if not restoring and abs(delta["kcal"]) < 4 and all(abs(delta[key]) < 1 for key in MACROS[1:]):
                continue
            if restoring and all(abs(value) < 1e-6 for value in delta.values()):
                continue

            meta = {
                key: value
                for key, value in (meal.meta or {}).items()
                if key not in {"training_carb_change", "training_replan_workouts"}
            }
            previous_carbs = float(meal.carbs_g or 0)
            if restoring and origin is not None:
                # A second fit can land on different gram amounts. Restore the
                # original saved numbers when the meal's prescription returns.
                if _meal_portions(meal) == origin["portions"]:
                    for key in ("training_replan", "training_replan_target", "training_replan_origin"):
                        meta.pop(key, None)
                    meal.meta = meta
                    changed = True
                    continue
                _restore_portions(meal, origin["portions"])
                target = origin["target"]
                meta.pop("training_replan_origin")
                status = "adjusted"
            else:
                if not origin:
                    origin = {
                        "weights": weights[slot],
                        "prescription": {key: round(value, 6) for key, value in old_targets[slot].items()},
                        "target": meta.get("training_replan_target", weights[slot]),
                        "portions": _meal_portions(meal),
                    }
                target = _target_from_origin(origin, prescription)
                status = _update_meal(meal, target, heuristic=plan.source == "heuristic")
                meta["training_replan_origin"] = origin
            if status == "review":
                meta["training_replan_workouts"] = _meal_workout_reasons(workouts, slot)
            actual_carbs = round(float(meal.carbs_g or 0) - previous_carbs, 1)
            if status == "adjusted" and actual_carbs:
                meta["training_carb_change"] = {
                    "grams": actual_carbs,
                    "workouts": _meal_workout_reasons(workouts, slot),
                }
            meal.meta = {
                **meta,
                "training_replan": status,
                "training_replan_target": target,
            }
            summary["adjusted_meals" if status == "adjusted" else "meals_to_review"] += 1
            changed = True
        if changed:
            plan.totals = {key: sum(_macros(meal)[key] for meal in plan.meals) for key in MACROS}
            plan.updated_at = datetime.utcnow()
