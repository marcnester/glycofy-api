# app/routers/plans.py
from __future__ import annotations
from typing import Optional, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import date, datetime
from app.db import SessionLocal
from app.models import User
from app.routers.auth import get_current_user
from app.services.planner import (
    Targets, DayPlan, compute_targets, generate_plan_meals, grocery_list_for,
    pick_swap, to_dict, MEAL_ORDER
)

router = APIRouter()

# Very lightweight in-memory store for MVP
_PLAN_STORE: Dict[tuple[int, str], DayPlan] = {}

def _age_years_from(dob: Optional[date]) -> int:
    if not dob:
        return 35
    today = date.today()
    yrs = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(14, min(90, yrs))

def _training_kcal_today(db) -> int:
    # MVP: If you already have activities stored in DB, you can sum today’s kcal.
    # To keep this router decoupled from Activities, we’ll return 0 and rely on
    # /imports/strava/sync updating totals in future iterations.
    return 0

def _mk_targets(diso: str, user: User, db) -> Targets:
    age = _age_years_from(user.dob)
    training = _training_kcal_today(db)
    return compute_targets(
        sex=user.sex or "male",
        height_cm=float(user.height_cm or 175.0),
        weight_kg=float(user.weight_kg or 75.0),
        age_years=age,
        goal=user.goal or "maintain",
        training_kcal=training,
    )

def _ensure_plan_exists(user_id: int, d_iso: str, diet_pref: str, user: User, db) -> DayPlan:
    key = (user_id, d_iso)
    if key in _PLAN_STORE:
        return _PLAN_STORE[key]

    # build targets + meals
    targets = _mk_targets(d_iso, user, db)
    meals = generate_plan_meals(diet_pref, targets)
    groc = grocery_list_for(meals)
    plan = DayPlan(date=d_iso, locked=False, targets=targets, meals=meals, grocery_list=groc)
    _PLAN_STORE[key] = plan
    return plan

@router.get("/today")
def plan_today(diet_pref: Optional[str] = None,
               current: User = Depends(get_current_user)):
    d_iso = date.today().isoformat()
    with SessionLocal() as db:
        desired = (diet_pref or current.diet_pref or "omnivore").lower()
        plan = _ensure_plan_exists(current.id, d_iso, desired, current, db)
        return to_dict(plan)

@router.get("/{d}")
def plan_get(d: str,
             diet_pref: Optional[str] = None,
             current: User = Depends(get_current_user)):
    # validate/normalize date
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date")
    with SessionLocal() as db:
        desired = (diet_pref or current.diet_pref or "omnivore").lower()
        plan = _ensure_plan_exists(current.id, d, desired, current, db)
        return to_dict(plan)

@router.post("/{d}/lock")
def plan_lock(d: str,
              lock: bool = Query(True),
              current: User = Depends(get_current_user)):
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date")
    key = (current.id, d)
    if key not in _PLAN_STORE:
        raise HTTPException(status_code=404, detail="plan_not_found")
    _PLAN_STORE[key].locked = bool(lock)
    return {"date": d, "locked": _PLAN_STORE[key].locked}

@router.post("/{d}/swap")
def plan_swap(d: str,
              meal_type: str = Query(..., pattern="^(breakfast|lunch|dinner|snack)$"),
              exclude: Optional[str] = None,
              current: User = Depends(get_current_user)):
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date")

    key = (current.id, d)
    if key not in _PLAN_STORE:
        raise HTTPException(status_code=404, detail="plan_not_found")
    plan = _PLAN_STORE[key]
    if plan.locked:
        raise HTTPException(status_code=400, detail="plan_locked")

    # Find current meal to swap & kcal hint
    current_meal = next((m for m in plan.meals if m.meal_type == meal_type), None)
    if not current_meal:
        raise HTTPException(status_code=400, detail="meal_type_not_in_plan")
    kcal_hint = current_meal.kcal

    # Build exclusion list
    exclude_titles = [t.strip() for t in (exclude or "").split(",") if t.strip()]
    if current_meal.title not in exclude_titles:
        exclude_titles.append(current_meal.title)

    # Diet pref – use user preference for replacement
    diet_pref = (current.diet_pref or "omnivore")
    new_m = pick_swap(diet_pref, meal_type, exclude_titles, kcal_hint)
    if not new_m:
        raise HTTPException(status_code=404, detail="no_alternative_found")

    # Replace meal in list
    plan.meals = [new_m if (m.meal_type == meal_type) else m for m in plan.meals]
    plan.grocery_list = grocery_list_for(plan.meals)
    return to_dict(plan)

@router.get("/{d}/grocery.txt")
def plan_grocery_txt(d: str,
                     current: User = Depends(get_current_user)):
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date")
    key = (current.id, d)
    if key not in _PLAN_STORE:
        raise HTTPException(status_code=404, detail="plan_not_found")
    plan = _PLAN_STORE[key]
    return "\n".join(plan.grocery_list)

@router.get("/{d}/grocery.csv")
def plan_grocery_csv(d: str,
                     current: User = Depends(get_current_user)):
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date")
    key = (current.id, d)
    if key not in _PLAN_STORE:
        raise HTTPException(status_code=404, detail="plan_not_found")

    # Build a minimal CSV safely (no backslashes in f-strings)
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["item"])
    for item in _PLAN_STORE[key].grocery_list:
        w.writerow([item])
    return buf.getvalue()