from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models_plan import Meal, MealItem, PlanDay


def upsert_plan(
    db: Session, user_id: int, on_date: date, targets: dict[str, int], window_used: int, meals_json: dict[str, Any]
):
    plan = db.query(PlanDay).filter(PlanDay.user_id == user_id, PlanDay.date == on_date).first()
    if not plan:
        plan = PlanDay(user_id=user_id, date=on_date)
        db.add(plan)

    plan.targets = dict(targets)
    plan.window_used = int(window_used)

    # replace meals
    plan.meals[:] = []  # delete-orphan cascade
    slot_order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}

    for idx, m in enumerate(meals_json.get("meals", [])):
        meal = Meal(
            slot=m.get("slot", "snack"),
            title=m.get("title") or m.get("slot", "Meal").capitalize(),
            instructions=m.get("instructions") or "",
            rank=slot_order.get(m.get("slot", "snack"), 3),
            source="llm" if m.get("source") == "llm" else "generated",
        )
        for it in m.get("items", []):
            meal.items.append(
                MealItem(
                    name=it.get("name", "Item"),
                    qty=it.get("qty"),
                    unit=it.get("unit"),
                    kcal=it.get("kcal"),
                    protein_g=it.get("protein_g"),
                    carbs_g=it.get("carbs_g"),
                    fat_g=it.get("fat_g"),
                )
            )
        plan.meals.append(meal)

    # recompute totals
    totals = {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for meal in plan.meals:
        msum = {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
        for it in meal.items:
            msum["kcal"] += int(it.kcal or 0)
            msum["protein_g"] += int(it.protein_g or 0)
            msum["carbs_g"] += int(it.carbs_g or 0)
            msum["fat_g"] += int(it.fat_g or 0)
        meal.kcal = msum["kcal"]
        meal.protein_g = msum["protein_g"]
        meal.carbs_g = msum["carbs_g"]
        meal.fat_g = msum["fat_g"]
        for k in totals:
            totals[k] += msum[k]

    plan.totals = totals
    db.commit()
    db.refresh(plan)
    return plan
