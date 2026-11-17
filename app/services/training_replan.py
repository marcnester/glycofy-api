"""Adjust saved meal portions when training inputs change."""

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Plan, PlanMeal, User
from app.services.training_nutrition import (
    MACROS,
    TrainingNutritionResult,
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
    if re.search(r"\d\s*(?:g|grams?|kg|mg|ml|liters?|oz|lb|cups?|tbsp|tsp)\b", meal.instructions or "", re.I):
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
        weights = {meal.meal_type: _macros(meal) for meal in plan.meals}
        old_targets = allocate_meal_targets(before[plan.date], weights)
        new_targets = allocate_meal_targets(after, weights)
        changed = False
        for meal in plan.meals:
            if meal.feedback and meal.feedback.outcome in {"eaten", "substituted", "skipped"}:
                continue
            slot = meal.meal_type
            delta = {key: new_targets[slot][key] - old_targets[slot][key] for key in MACROS}
            if abs(delta["kcal"]) < 4 and all(abs(delta[key]) < 1 for key in MACROS[1:]):
                continue
            base = (meal.meta or {}).get("training_replan_target", weights[slot])
            target = {key: round(max(0.0, base[key] + delta[key]), 1) for key in MACROS}
            status = _update_meal(meal, target, heuristic=plan.source == "heuristic")
            meal.meta = {
                **(meal.meta or {}),
                "training_replan": status,
                "training_replan_target": target,
            }
            summary["adjusted_meals" if status == "adjusted" else "meals_to_review"] += 1
            changed = True
        if changed:
            plan.totals = {key: sum(_macros(meal)[key] for meal in plan.meals) for key in MACROS}
            plan.updated_at = datetime.utcnow()
