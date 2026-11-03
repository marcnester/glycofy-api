"""Live preparation guidance; nutrition and groceries always describe eaten portions.

Links are valid only while both stored meals match the generated recipes. Swapping
or editing either meal therefore falls back to fresh-cooking instructions instead
of promising leftovers that no longer exist. No duplicate batch grocery rows.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from sqlalchemy.orm import Session, object_session

from app.models import Plan, PlanMeal


def meal_signature(meal: PlanMeal) -> str:
    ingredients = sorted(
        ((item.name, float(item.qty) if item.qty is not None else None, item.unit) for item in meal.items), key=repr
    )
    value = [meal.title, meal.instructions, ingredients]
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _valid_source(db: Session, meal: PlanMeal) -> PlanMeal | None:
    link = (meal.meta or {}).get("leftovers")
    if not isinstance(link, dict) or meal_signature(meal) != link.get("portion_signature"):
        return None
    source_id = link.get("source_meal_id")
    if not isinstance(source_id, int) or source_id <= 0:
        return None
    source = db.get(PlanMeal, source_id)
    if (
        source is None
        or source.plan.user_id != meal.plan.user_id
        or meal.plan.date != source.plan.date + timedelta(days=1)
        or source.meal_type != "dinner"
        or meal.meal_type != "lunch"
        or meal_signature(source) != link.get("source_signature")
    ):
        return None
    return source


def link_week_leftovers(db: Session, user_id: int, finalized: dict, applied_dates: set[str]) -> None:
    """Link only meals actually persisted by this generation, after rebalancing."""
    db.flush()
    plans = {
        plan.date.isoformat(): plan
        for plan in db.query(Plan)
        .filter(Plan.user_id == user_id, Plan.date.in_([date.fromisoformat(day) for day in applied_dates]))
        .all()
        if plan.date.isoformat() in applied_dates
    }
    for day, slots in finalized.items():
        lunch = slots.get("lunch")
        source_day = (lunch.meta or {}).get("leftover_source_date") if lunch else None
        if day not in plans or source_day not in plans:
            continue
        consumer = next((meal for meal in plans[day].meals if meal.meal_type == "lunch"), None)
        source = next((meal for meal in plans[source_day].meals if meal.meal_type == "dinner"), None)
        if consumer is None or source is None or consumer.title != source.title:
            continue
        consumer.meta = {
            **(consumer.meta or {}),
            "leftovers": {
                "source_meal_id": source.id,
                "source_date": source_day,
                "source_signature": meal_signature(source),
                "portion_signature": meal_signature(consumer),
            },
        }


def preparation_for_meal(meal: PlanMeal) -> dict | None:
    db = object_session(meal)
    if db is None:
        return None
    source = _valid_source(db, meal)
    if source is not None:
        return {
            "kind": "leftovers",
            "source_date": source.plan.date.isoformat(),
            "note": f"Lunch is covered: use the portion reserved from dinner on {source.plan.date.isoformat()}.",
            "instructions": "Use the refrigerated portion reserved for this lunch. Reheat leftovers to 165°F / 74°C throughout before serving.",
            "prep_time_min": 2,
            "cook_time_min": 8,
            "total_time_min": 10,
        }
    if meal.meal_type != "dinner":
        return None
    following = (
        db.query(PlanMeal)
        .join(Plan)
        .filter(
            Plan.user_id == meal.plan.user_id,
            Plan.date == meal.plan.date + timedelta(days=1),
            PlanMeal.meal_type == "lunch",
        )
        .all()
    )
    consumers = [row for row in following if _valid_source(db, row) is meal]
    if not consumers:
        return None
    # Quantities remain grams from verified generation; display them separately
    # from dinner's eaten portion so users don't log the entire cooking batch.
    combined: dict[tuple[str, str], float] = {}
    reserve = []
    for row in [meal, *consumers]:
        for item in row.items:
            if item.qty is None or not item.unit:
                return None
            key = (item.name, item.unit)
            combined[key] = combined.get(key, 0) + item.qty
            if row is not meal:
                reserve.append({"name": item.name, "quantity": item.qty, "unit": item.unit})
    dinner_weight = sum(item.qty for item in meal.items)
    lunch_weight = sum(item.qty for row in consumers for item in row.items)
    reserve_percent = round(100 * lunch_weight / (dinner_weight + lunch_weight))
    return {
        "kind": "batch",
        "note": "Cook dinner and tomorrow's lunch together. Nutrition below is for dinner only.",
        "batch_ingredients": [
            {"name": name, "quantity": round(qty, 2), "unit": unit} for (name, unit), qty in combined.items()
        ],
        "reserve_ingredients": reserve,
        "storage": f"Set aside about {reserve_percent}% of the cooked batch for tomorrow’s lunch before serving dinner. Refrigerate promptly, within 2 hours.",
    }
