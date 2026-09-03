# ---------- helper to apply free-form AI idea to a meal ----------


# app/routers/plans.py
from __future__ import annotations

import re
from datetime import date as date_cls
from datetime import datetime, timedelta
from math import ceil
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import GroceryApproval, Plan, PlanItem, PlanMeal, Recipe, User
from app.routers.plan_models import EnergyTarget, UserPreference

router = APIRouter()

# ---------------------------
# Shared helpers
# ---------------------------


def _parse_iso_date(value: str) -> date_cls:
    try:
        return date_cls.fromisoformat(value)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format: {value}. Expected YYYY-MM-DD",
        )


def _safe_meal_type(value: str | None) -> str:
    mt = (value or "").strip().lower()
    if mt in {"breakfast", "lunch", "dinner", "snack"}:
        return mt
    return "snack"


def _default_meal_order(meal_type: str) -> int:
    return {
        "breakfast": 1,
        "lunch": 2,
        "dinner": 3,
        "snack": 4,
    }.get(_safe_meal_type(meal_type), 99)


def _aggregate_totals(meals) -> dict[str, float]:
    totals = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for meal in meals or []:
        totals["kcal"] += float(getattr(meal, "kcal", None) or 0)
        totals["protein_g"] += float(getattr(meal, "protein_g", None) or 0)
        totals["carbs_g"] += float(getattr(meal, "carbs_g", None) or 0)
        totals["fat_g"] += float(getattr(meal, "fat_g", None) or 0)
    return totals


def _plan_to_dict(plan: Plan) -> dict[str, Any]:
    meals = sorted(
        list(getattr(plan, "meals", []) or []),
        key=lambda m: ((getattr(m, "order_index", None) or 99), (getattr(m, "id", None) or 0)),
    )

    return {
        "id": plan.id,
        "date": plan.date.isoformat() if getattr(plan, "date", None) else None,
        "locked": bool(getattr(plan, "locked", False)),
        "totals": getattr(plan, "totals", None) or _aggregate_totals(meals),
        "source": getattr(plan, "source", None),
        "meals": [
            {
                "id": m.id,
                "meal_type": m.meal_type,
                "title": m.title,
                "kcal": m.kcal,
                "protein_g": m.protein_g,
                "carbs_g": m.carbs_g,
                "fat_g": m.fat_g,
                "instructions": m.instructions,
                "tags": m.tags or [],
                "order_index": m.order_index,
                "recipe_id": getattr(m, "recipe_id", None),
                # WHY BUTTON FIX: return persisted AI explanation to the UI.
                "meta": getattr(m, "meta", None) or {},
                "ingredients": [
                    {
                        "id": getattr(i, "id", None) if not isinstance(i, dict) else i.get("id"),
                        "name": (i.name if not isinstance(i, dict) else i.get("name")) or "Item",
                        "qty": i.qty if not isinstance(i, dict) else i.get("qty", i.get("quantity", i.get("amount"))),
                        "unit": i.unit if not isinstance(i, dict) else i.get("unit"),
                        "kcal": i.kcal if not isinstance(i, dict) else i.get("kcal"),
                        "protein_g": i.protein_g if not isinstance(i, dict) else i.get("protein_g"),
                        "carbs_g": i.carbs_g if not isinstance(i, dict) else i.get("carbs_g"),
                        "fat_g": i.fat_g if not isinstance(i, dict) else i.get("fat_g"),
                        "meta": (i.meta if not isinstance(i, dict) else i.get("meta")) or {},
                    }
                    for i in _display_ingredients_for_meal(m)
                ],
                # Backward-compatible alias for older UI code.
                "items": [
                    {
                        "id": getattr(i, "id", None),
                        "name": i.name,
                        "qty": i.qty,
                        "unit": i.unit,
                        "kcal": i.kcal,
                        "protein_g": i.protein_g,
                        "carbs_g": i.carbs_g,
                        "fat_g": i.fat_g,
                        "meta": i.meta or {},
                    }
                    for i in (getattr(m, "items", []) or [])
                ],
            }
            for m in meals
        ],
        "created_at": plan.created_at.isoformat() if getattr(plan, "created_at", None) else None,
        "updated_at": plan.updated_at.isoformat() if getattr(plan, "updated_at", None) else None,
    }


_HEURISTIC_INGREDIENT_NAMES = {"protein (lean)", "complex carbs", "fats (healthy)"}


def _display_ingredients_for_meal(meal: PlanMeal) -> list[Any]:
    """Use linked recipe ingredients for plans created before AI item persistence was fixed."""
    items = list(getattr(meal, "items", []) or [])
    names = {str(getattr(item, "name", "")).strip().lower() for item in items}
    if names and not names.issubset(_HEURISTIC_INGREDIENT_NAMES):
        return items

    recipe = getattr(meal, "recipe", None)
    recipe_ingredients = _extract_recipe_ingredients(recipe) if recipe is not None else []
    if not recipe_ingredients:
        return items
    return [item if isinstance(item, dict) else {"name": str(item).strip()} for item in recipe_ingredients]


def _coerce_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _extract_recipe_ingredients(recipe: Recipe) -> list[Any]:
    for attr in ("ingredients", "items"):
        value = getattr(recipe, attr, None)
        if value:
            return value
    meta = getattr(recipe, "meta", None) or {}
    if isinstance(meta, dict):
        for key in ("ingredients", "items"):
            value = meta.get(key)
            if value:
                return value
    return []


def _apply_recipe_to_meal(meal: PlanMeal, recipe: Recipe) -> None:
    meal.meal_type = _safe_meal_type(getattr(recipe, "meal_type", None) or meal.meal_type)
    meal.title = getattr(recipe, "title", None) or meal.title or meal.meal_type.title()
    meal.kcal = getattr(recipe, "kcal", None)
    meal.protein_g = getattr(recipe, "protein_g", None)
    meal.carbs_g = getattr(recipe, "carbs_g", None)
    meal.fat_g = getattr(recipe, "fat_g", None)
    meal.instructions = getattr(recipe, "instructions", None)
    meal.tags = _coerce_tags(getattr(recipe, "diet_tags", None))
    meal.recipe_id = getattr(recipe, "id", None)
    meal.updated_at = datetime.utcnow()

    for old in list(getattr(meal, "items", []) or []):
        meal.items.remove(old)

    for ing in _extract_recipe_ingredients(recipe):
        name = "Item"
        qty = None
        unit = None
        kcal = None
        protein_g = None
        carbs_g = None
        fat_g = None
        meta: dict[str, Any] = {}

        if isinstance(ing, dict):
            name = str(ing.get("name") or ing.get("ingredient") or ing.get("item") or "Item").strip()
            qty = ing.get("qty") or ing.get("quantity")
            unit = ing.get("unit")
            kcal = ing.get("kcal")
            protein_g = ing.get("protein_g")
            carbs_g = ing.get("carbs_g")
            fat_g = ing.get("fat_g")
            meta = ing.get("meta") or {}
        elif isinstance(ing, str):
            name = ing.strip() or "Item"
        else:
            name = str(ing).strip() or "Item"

        meal.items.append(
            PlanItem(
                name=name,
                qty=qty,
                unit=unit,
                kcal=kcal,
                protein_g=protein_g,
                carbs_g=carbs_g,
                fat_g=fat_g,
                meta=meta,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )


# ---------------------------
# Pydantic Schemas (input)
# ---------------------------


class ItemIn(BaseModel):
    name: str
    qty: float | None = None
    unit: str | None = None
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class MealIn(BaseModel):
    meal_type: str
    title: str | None = None
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    instructions: str | None = None
    tags: list[str] | None = None
    order_index: int | None = None
    ingredients: list[ItemIn] = Field(default_factory=list)


class PlanCreateIn(BaseModel):
    totals: dict[str, Any] | None = None
    meals: list[MealIn] = Field(default_factory=list)
    source: str | None = "heuristic"


class PlanPatchIn(BaseModel):
    locked: bool | None = None
    totals: dict[str, Any] | None = None


# -------- Apply Recommendations payload (supports recipe_id, pick_id, ai_idea, new_recipe) --------


class AIIngredientPayload(BaseModel):
    name: str
    amount: str | None = None  # free-form like "1 cup", "100 grams"


class AIIdeaPayload(BaseModel):
    title: str | None = None
    description: str | None = None
    approx_macros: dict[str, float] | None = None  # {kcal, protein_g, carbs_g, fat_g}
    # Flexible: may be list of AIIngredientPayload OR list of strings from LLM
    ingredients: list[Any] = Field(default_factory=list)
    # Optional step-wise instructions from the LLM (list of short steps)
    instructions: list[str] | None = None
    total_time_min: int | None = Field(default=None, ge=1, le=240)
    # Optional protein group ("fish", "plant", etc.) if we ever need it
    protein_group: str | None = None


class LLMNewRecipe(BaseModel):
    """
    Shape for new recipes created by the LLM (per-slot pick-or-create):

      {
        "title": "High-protein oatmeal",
        "ingredients": ["80 g oats", "30 g whey protein", ...],
        "instructions": ["Do X", "Do Y"],
        "protein_group": "plant",
        "macro_estimate": { "kcal": 620, "protein_g": 40, "carbs_g": 70, "fat_g": 15 }
      }
    """

    title: str
    ingredients: list[Any] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    total_time_min: int | None = Field(default=None, ge=1, le=240)
    protein_group: str | None = None
    macro_estimate: dict[str, float] | None = None


class ApplyItem(BaseModel):
    slot: str = Field(..., description="breakfast|lunch|dinner|snack")

    # Legacy + primary path: direct catalog assignment
    recipe_id: int | None = Field(default=None, description="Catalog recipe id to apply to this slot")

    # WHY BUTTON FIX: AI explanation returned by /v1/llm/recommend.
    reason: str | None = None

    # Legacy AI idea path (front-end builds from LLM meta.ai_idea)
    ai_idea: AIIdeaPayload | None = Field(
        default=None,
        description="Free-form AI idea to persist when no catalog recipe is provided",
    )

    # New LLM pick-or-create hints (optional; extra fields are safe)
    mode: Literal["pick", "create"] | None = None
    pick_id: int | None = None  # chosen catalog id (equivalent to recipe_id)
    new_recipe: LLMNewRecipe | None = None  # direct AI recipe when no catalog match


class ApplyRecommendationsIn(BaseModel):
    items: list[ApplyItem] = Field(default_factory=list)


# ---------------------------
# Heuristic seed (stub)
# ---------------------------


def _seed_plan_heuristic(db: Session, user: User, day: date_cls) -> dict[str, Any]:
    target: EnergyTarget | None = (
        db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
    )
    kcal = float((target.target_kcal if target else None) or (target.tdee_kcal if target else None) or 2400.0)

    # Macro split (high-protein bias for athletes)
    p = max(100.0, round(kcal * 0.30 / 4))  # grams
    c = round(kcal * 0.45 / 4)
    f = round(kcal * 0.25 / 9)

    splits = {
        "breakfast": 0.25,
        "lunch": 0.30,
        "dinner": 0.30,
        "snack": 0.15,
    }

    meals: list[dict[str, Any]] = []
    for mt in ["breakfast", "lunch", "dinner", "snack"]:
        frac = splits[mt]
        mkcal = round(kcal * frac)
        mp = round(p * frac)
        mc = round(c * frac)
        mf = round(f * frac)
        meals.append(
            {
                "meal_type": mt,
                "title": mt.title(),
                "kcal": mkcal,
                "protein_g": mp,
                "carbs_g": mc,
                "fat_g": mf,
                "instructions": None,
                "tags": [],
                "order_index": _default_meal_order(mt),
                "ingredients": [
                    {"name": "Protein (lean)", "qty": 1, "unit": "serv", "protein_g": mp * 0.7},
                    {"name": "Complex carbs", "qty": 1, "unit": "serv", "carbs_g": mc * 0.7},
                    {"name": "Fats (healthy)", "qty": 1, "unit": "serv", "fat_g": mf * 0.7},
                ],
            }
        )

    return {
        "totals": {"kcal": kcal, "protein_g": p, "carbs_g": c, "fat_g": f},
        "meals": meals,
        "source": "heuristic",
    }


# ---------------------------
# LLM seam (future)
# ---------------------------


async def generate_meal_plan_llm(
    db: Session,
    user: User,
    day: date_cls,
    preferences: UserPreference | None,
    targets: EnergyTarget | None,
) -> dict[str, Any]:
    return _seed_plan_heuristic(db, user, day)


def _apply_ai_idea_to_meal(meal: PlanMeal, ai: AIIdeaPayload, slot: str) -> None:
    slot_norm = _safe_meal_type(slot or meal.meal_type or "snack")

    title = (ai.title or "").strip() or (ai.description or "").strip().split("\n")[0] or slot_norm.title()

    instructions_text: str | None = None
    try:
        inst_list = getattr(ai, "instructions", None) or []
        parts = [str(p).strip() for p in inst_list if p and str(p).strip()]
        if parts:
            instructions_text = "\n".join(parts)
        elif ai.description and ai.description.strip():
            instructions_text = ai.description.strip()
    except Exception:
        instructions_text = ai.description.strip() if ai.description and ai.description.strip() else None

    macros = ai.approx_macros or {}
    kcal = macros.get("kcal")
    protein_g = macros.get("protein_g")
    carbs_g = macros.get("carbs_g")
    fat_g = macros.get("fat_g")

    meal.meal_type = slot_norm
    meal.title = title
    meal.instructions = instructions_text
    if ai.total_time_min:
        meal.meta = {**(meal.meta or {}), "total_time_min": ai.total_time_min}

    if kcal is not None:
        meal.kcal = float(kcal)
    if protein_g is not None:
        meal.protein_g = float(protein_g)
    if carbs_g is not None:
        meal.carbs_g = float(carbs_g)
    if fat_g is not None:
        meal.fat_g = float(fat_g)

    meal.recipe_id = None

    # Clear existing items
    for old in list(meal.items):
        meal.items.remove(old)

    # 🔥 FIXED INGREDIENT HANDLING
    for ing in getattr(ai, "ingredients", None) or []:
        name = "Item"
        amount: str | None = None

        # ✅ HANDLE DICT FIRST (CRITICAL)
        if isinstance(ing, dict):
            name = ing.get("name") or ing.get("ingredient") or ing.get("item") or "Item"
            name = str(name).strip()

            amount = ing.get("amount") or ing.get("qty") or ing.get("quantity") or ""
            amount = str(amount).strip() or None

        # ✅ HANDLE Pydantic objects
        elif hasattr(ing, "name") and not isinstance(ing, dict):
            try:
                name = (getattr(ing, "name", "") or "Item").strip()
                amount = (getattr(ing, "amount", "") or "").strip() or None
            except Exception:
                name = "Item"
                amount = None

        # ✅ HANDLE strings
        elif isinstance(ing, str):
            name = ing.strip() or "Item"
            amount = None

        # ✅ fallback
        else:
            name = str(ing).strip() or "Item"
            amount = None

        qty_val: float | None = None
        unit_val: str | None = None

        if amount:
            parts = amount.split()
            try:
                qty_val = float(parts[0])
                unit_val = " ".join(parts[1:]) or None
            except ValueError:
                qty_val = None
                unit_val = amount

        meal.items.append(
            PlanItem(
                name=name,
                qty=qty_val,
                unit=unit_val,
                kcal=None,
                protein_g=None,
                carbs_g=None,
                fat_g=None,
                meta={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )


# ---------------------------
# Endpoints
# ---------------------------


@router.get("/{date}", response_model=dict[str, Any])
async def get_plan(
    date: str = Path(..., description="YYYY-MM-DD"),
    create_if_missing: bool = Query(False, description="If true, create plan when missing"),
    engine: str = Query(
        "heuristic",
        description="engine used when create_if_missing=true (heuristic|llm)",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if engine not in ("heuristic", "llm"):
        raise HTTPException(status_code=400, detail="engine must be 'heuristic' or 'llm'")

    day = _parse_iso_date(date)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if plan:
        return _plan_to_dict(plan)

    if not create_if_missing:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Create on the fly
    if engine == "llm":
        pref = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        target = db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
        seed = await generate_meal_plan_llm(db, user, day, pref, target)
    else:
        seed = _seed_plan_heuristic(db, user, day)

    new_plan = Plan(
        user_id=user.id,
        date=day,
        locked=False,
        totals=seed.get("totals") or {},
        source=(seed.get("source") or engine or "heuristic"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(new_plan)
    db.flush()

    for m in seed.get("meals", []):
        meal = PlanMeal(
            plan_id=new_plan.id,
            meal_type=_safe_meal_type(m.get("meal_type")),
            title=m.get("title"),
            kcal=m.get("kcal"),
            protein_g=m.get("protein_g"),
            carbs_g=m.get("carbs_g"),
            fat_g=m.get("fat_g"),
            instructions=m.get("instructions"),
            tags=m.get("tags") or [],
            order_index=(
                m.get("order_index")
                if isinstance(m.get("order_index"), int)
                else _default_meal_order(m.get("meal_type") or "")
            ),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            # NEW: seed meals don't have a specific catalog recipe
            recipe_id=None,
        )
        db.add(meal)
        db.flush()
        for it in m.get("ingredients") or []:
            item = PlanItem(
                meal_id=meal.id,
                name=it.get("name") or "Item",
                qty=it.get("qty"),
                unit=it.get("unit"),
                kcal=it.get("kcal"),
                protein_g=it.get("protein_g"),
                carbs_g=it.get("carbs_g"),
                fat_g=it.get("fat_g"),
                meta=it.get("meta") or {},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(item)

    if not new_plan.totals:
        new_plan.totals = _aggregate_totals(new_plan.meals)

    db.commit()
    db.refresh(new_plan)
    return _plan_to_dict(new_plan)


@router.post("/{date}", response_model=dict[str, Any])
async def create_or_replace_plan(
    date: str,
    payload: PlanCreateIn = Body(default_factory=PlanCreateIn),
    engine: str = Query(default="heuristic"),
    replace: bool = Query(default=True, description="If true, replace existing plan for the day"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if engine not in ("heuristic", "llm"):
        raise HTTPException(status_code=400, detail="engine must be 'heuristic' or 'llm'")

    day = _parse_iso_date(date)

    if replace:
        existing = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
        if existing:
            db.delete(existing)
            db.commit()

    if payload.meals:
        seed = {
            "totals": payload.totals or {},
            "meals": [m.model_dump() for m in payload.meals],
            "source": payload.source or engine,
        }
    elif engine == "llm":
        pref = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        target = db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
        seed = await generate_meal_plan_llm(db, user, day, pref, target)
    else:
        seed = _seed_plan_heuristic(db, user, day)

    plan = Plan(
        user_id=user.id,
        date=day,
        locked=False,
        totals=seed.get("totals") or {},
        source=(seed.get("source") or engine or "heuristic"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(plan)
    db.flush()

    for m in seed.get("meals", []):
        meal = PlanMeal(
            plan_id=plan.id,
            meal_type=_safe_meal_type(m.get("meal_type")),
            title=m.get("title"),
            kcal=m.get("kcal"),
            protein_g=m.get("protein_g"),
            carbs_g=m.get("carbs_g"),
            fat_g=m.get("fat_g"),
            instructions=m.get("instructions"),
            tags=m.get("tags") or [],
            order_index=(
                m.get("order_index")
                if isinstance(m.get("order_index"), int)
                else _default_meal_order(m.get("meal_type") or "")
            ),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            # NEW:
            recipe_id=None,
        )
        db.add(meal)
        db.flush()

        for it in m.get("ingredients") or []:
            item = PlanItem(
                meal_id=meal.id,
                name=it.get("name") or "Item",
                qty=it.get("qty"),
                unit=it.get("unit"),
                kcal=it.get("kcal"),
                protein_g=it.get("protein_g"),
                carbs_g=it.get("carbs_g"),
                fat_g=it.get("fat_g"),
                meta=it.get("meta") or {},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(item)

    if not plan.totals:
        plan.totals = _aggregate_totals(plan.meals)

    db.commit()
    db.refresh(plan)
    return _plan_to_dict(plan)


@router.patch("/{date}", response_model=dict[str, Any])
def patch_plan(
    date: str,
    payload: PlanPatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = _parse_iso_date(date)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if payload.locked is not None:
        plan.locked = bool(payload.locked)
    if payload.totals is not None:
        plan.totals = payload.totals
    plan.updated_at = datetime.utcnow()

    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_to_dict(plan)


@router.post("/{date}/lock", response_model=dict[str, Any])
def lock_toggle(
    date: str,
    lock: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = _parse_iso_date(date)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan.locked = bool(lock)
    plan.updated_at = datetime.utcnow()
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_to_dict(plan)


@router.post("/{date}/regenerate", response_model=dict[str, Any])
async def regenerate_plan(
    date: str,
    engine: str = Query(default="heuristic"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Regenerate the plan for a given day using the requested engine, without
    deleting the Plan row itself.
    """
    if engine not in ("heuristic", "llm"):
        raise HTTPException(status_code=400, detail="engine must be 'heuristic' or 'llm'")

    day = _parse_iso_date(date)
    plan: Plan | None = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if plan.locked:
        raise HTTPException(status_code=400, detail="Plan is locked")

    # ---- Hard reset meals/items for this plan ----
    subq = db.query(PlanMeal.id).filter(PlanMeal.plan_id == plan.id).subquery()
    db.query(PlanItem).filter(PlanItem.meal_id.in_(subq)).delete(synchronize_session=False)
    db.query(PlanMeal).filter(PlanMeal.plan_id == plan.id).delete(synchronize_session=False)
    plan.meals = []  # keep in-memory state consistent
    db.flush()

    # ---- Reseed via chosen engine ----
    if engine == "llm":
        pref = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        target = db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
        seed = await generate_meal_plan_llm(db, user, day, pref, target)
    else:
        seed = _seed_plan_heuristic(db, user, day)

    for m in seed.get("meals", []):
        meal = PlanMeal(
            plan_id=plan.id,
            meal_type=_safe_meal_type(m.get("meal_type")),
            title=m.get("title"),
            kcal=m.get("kcal"),
            protein_g=m.get("protein_g"),
            carbs_g=m.get("carbs_g"),
            fat_g=m.get("fat_g"),
            instructions=m.get("instructions"),
            tags=m.get("tags") or [],
            order_index=(
                m.get("order_index")
                if isinstance(m.get("order_index"), int)
                else _default_meal_order(m.get("meal_type") or "")
            ),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            # NEW:
            recipe_id=None,
        )
        db.add(meal)
        db.flush()

        for it in m.get("ingredients") or []:
            db.add(
                PlanItem(
                    meal_id=meal.id,
                    name=it.get("name") or "Item",
                    qty=it.get("qty"),
                    unit=it.get("unit"),
                    kcal=it.get("kcal"),
                    protein_g=it.get("protein_g"),
                    carbs_g=it.get("carbs_g"),
                    fat_g=it.get("fat_g"),
                    meta=it.get("meta") or {},
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )

    plan.totals = seed.get("totals") or _aggregate_totals(plan.meals)
    plan.source = seed.get("source") or engine
    plan.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(plan)
    return _plan_to_dict(plan)


# ---------------------------
# Assign a recipe to a specific meal
# ---------------------------


@router.post("/{date}/meals/{meal_id}/assign_recipe/{recipe_id}", response_model=dict[str, Any])
def assign_recipe_to_meal(
    date: str,
    meal_id: int = Path(..., ge=1),
    recipe_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = _parse_iso_date(date)
    plan: Plan | None = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    meal = next((m for m in plan.meals if m.id == meal_id), None)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found in this plan")

    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    if plan.locked:
        raise HTTPException(status_code=400, detail="Plan is locked")

    _apply_recipe_to_meal(meal, recipe)
    plan.updated_at = datetime.utcnow()

    # Refresh totals after assignment
    plan.totals = _aggregate_totals(plan.meals)

    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_to_dict(plan)


# ---------------------------
# Apply AI / catalog recommendations to the plan
# ---------------------------


@router.post("/{date}/apply_recommendations", response_model=dict[str, Any])
def apply_recommendations(
    date: str,
    payload: ApplyRecommendationsIn = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Accepts:
      {"items":[
         {"slot":"breakfast","recipe_id":1},
         {"slot":"lunch","ai_idea":{...}},
         {"slot":"dinner","pick_id":42},
         {"slot":"snack","new_recipe":{...}},
         ...
       ]}

    Semantics:

      - The payload is treated as a **full-day replacement** for this date.
      - If the plan is unlocked:
          * We first validate all recipe_ids / pick_ids exist.
          * We then delete ALL existing PlanMeals/PlanItems for this plan.
          * We create new PlanMeals for each item (by slot), applying either
            a catalog Recipe (recipe_id/pick_id) or a free-form AI idea
            (ai_idea or new_recipe).
          * Totals are recomputed and source is set to "llm".

      - If the plan does not yet exist, it is created.
      - If the plan is locked → 400 "Plan is locked".
    """
    day = _parse_iso_date(date)
    plan: Plan | None = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()

    # Create a Plan if missing; weekly LLM apply can rely on this.
    if not plan:
        plan = Plan(
            user_id=user.id,
            date=day,
            locked=False,
            totals={},
            source="llm",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(plan)
        db.flush()

    if plan.locked:
        raise HTTPException(status_code=400, detail="Plan is locked")

    # If there are no items or no actual assignments, treat as no-op.
    if not payload.items:
        return _plan_to_dict(plan)

    has_any_assignment = any(
        (it.recipe_id is not None)
        or (it.ai_idea is not None)
        or (it.pick_id is not None)
        or (it.new_recipe is not None)
        for it in payload.items
    )
    if not has_any_assignment:
        return _plan_to_dict(plan)

    # ---- Validate all recipe_ids / pick_ids up-front before mutating the plan ----
    recipe_ids: list[int] = []
    for it in payload.items:
        if it.recipe_id is not None:
            recipe_ids.append(int(it.recipe_id))
        elif it.pick_id is not None:
            recipe_ids.append(int(it.pick_id))

    recipes_by_id: dict[int, Recipe] = {}
    if recipe_ids:
        rows: list[Recipe] = db.query(Recipe).filter(Recipe.id.in_(recipe_ids)).all()
        recipes_by_id = {r.id: r for r in rows}
        missing = sorted(set(recipe_ids) - set(recipes_by_id.keys()))
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Recipe id(s) {missing} not found",
            )

    # ---- Hard reset meals/items for this plan (we're doing a full-day rewrite) ----
    subq = db.query(PlanMeal.id).filter(PlanMeal.plan_id == plan.id).subquery()
    db.query(PlanItem).filter(PlanItem.meal_id.in_(subq)).delete(synchronize_session=False)
    db.query(PlanMeal).filter(PlanMeal.plan_id == plan.id).delete(synchronize_session=False)
    plan.meals = []
    db.flush()

    # ---- Create new meals for each ApplyItem ----
    created_any = False
    for it in payload.items:
        slot = _safe_meal_type(it.slot)

        meal = PlanMeal(
            plan_id=plan.id,
            meal_type=slot,
            title=None,
            kcal=None,
            protein_g=None,
            carbs_g=None,
            fat_g=None,
            instructions=None,
            tags=[],
            order_index=_default_meal_order(slot),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            # NEW: default to no backing recipe until we know
            recipe_id=None,
        )
        db.add(meal)
        db.flush()

        # Determine effective recipe id (recipe_id or pick_id)
        effective_recipe_id: int | None = None
        if it.recipe_id is not None:
            effective_recipe_id = int(it.recipe_id)
        elif it.pick_id is not None:
            effective_recipe_id = int(it.pick_id)

        if effective_recipe_id is not None:
            recipe = recipes_by_id.get(effective_recipe_id)
            if not recipe:
                raise HTTPException(
                    status_code=400,
                    detail=f"Recipe id {effective_recipe_id} not found",
                )

            _apply_recipe_to_meal(meal, recipe)

            # WHY BUTTON FIX: persist the LLM explanation on the meal so
            # /v1/plan/{date} can return it and the UI Why? button can display it.
            meal.meta = {
                **(getattr(meal, "meta", None) or {}),
                "reason": it.reason,
            }

            created_any = True
        elif it.ai_idea is not None:
            _apply_ai_idea_to_meal(meal, it.ai_idea, slot)
            meal.meta = {
                **(getattr(meal, "meta", None) or {}),
                "reason": it.reason,
            }
            created_any = True
        elif it.new_recipe is not None:
            # Convert new_recipe into an AIIdeaPayload and reuse the same helper
            nr = it.new_recipe
            ai_from_new = AIIdeaPayload(
                title=nr.title,
                description=None,
                approx_macros=nr.macro_estimate or {},
                ingredients=nr.ingredients or [],
                instructions=nr.instructions or [],
                total_time_min=nr.total_time_min,
                protein_group=nr.protein_group,
            )
            _apply_ai_idea_to_meal(meal, ai_from_new, slot)
            meal.meta = {
                **(getattr(meal, "meta", None) or {}),
                "reason": it.reason,
            }
            created_any = True

        plan.meals.append(meal)

    if created_any:
        plan.totals = _aggregate_totals(plan.meals)
        plan.source = "llm"
        plan.updated_at = datetime.utcnow()
        db.add(plan)
        db.commit()
        db.refresh(plan)

    return _plan_to_dict(plan)


# ---------------------------
# Suggest recipes for a meal
# ---------------------------


@router.get("/{date}/suggest", response_model=dict[str, Any])
def suggest_recipes_for_day(
    date: str,
    meal_type: str = Query(..., description="breakfast|lunch|dinner|snack"),
    # optional macro targets with tolerance filtering
    kcal: float | None = Query(None),
    protein_g: float | None = Query(None),
    carbs_g: float | None = Query(None),
    fat_g: float | None = Query(None),
    tolerance_pct: float = Query(0.25, ge=0.0, le=1.0, description="± tolerance as fraction (0.25 = ±25%)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=250),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ = _parse_iso_date(date)  # validate format; not used otherwise
    mt = _safe_meal_type(meal_type)

    qry = db.query(Recipe).filter(Recipe.meal_type == mt)

    def _within(col, target):
        if target is None:
            return qry
        low = target * (1 - tolerance_pct)
        high = target * (1 + tolerance_pct)
        return qry.filter(col >= low, col <= high)

    qry = _within(Recipe.kcal, kcal)
    qry = _within(Recipe.protein_g, protein_g)
    qry = _within(Recipe.carbs_g, carbs_g)
    qry = _within(Recipe.fat_g, fat_g)

    total = qry.count()
    items: list[Recipe] = qry.order_by(Recipe.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    def _recipe_to_dict(r: Recipe) -> dict[str, Any]:
        return {
            "id": r.id,
            "title": r.title,
            "meal_type": r.meal_type,
            "diet_tags": r.diet_tags,
            "kcal": r.kcal,
            "protein_g": r.protein_g,
            "carbs_g": r.carbs_g,
            "fat_g": r.fat_g,
        }

    return {
        "meal_type": mt,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_recipe_to_dict(r) for r in items],
    }


# ---------------------------
# Grocery exports
# ---------------------------


def _iter_items(plan: Plan):
    for m in plan.meals:
        for it in m.items:
            yield (m, it)


_GROCERY_CATEGORY_KEYWORDS = {
    "Produce": {
        "apple",
        "asparagus",
        "avocado",
        "banana",
        "basil",
        "berries",
        "berry",
        "blueberry",
        "broccoli",
        "cabbage",
        "carrot",
        "celery",
        "cherry tomato",
        "cilantro",
        "corn",
        "cucumber",
        "garlic",
        "ginger",
        "greens",
        "kale",
        "lemon",
        "lettuce",
        "lime",
        "mushroom",
        "onion",
        "parsley",
        "pepper",
        "pineapple",
        "spinach",
        "sweet potato",
        "tomato",
    },
    "Meat & Seafood": {
        "beef",
        "chicken",
        "cod",
        "fish",
        "pork",
        "salmon",
        "scallop",
        "shrimp",
        "steak",
        "tuna",
        "turkey",
    },
    "Dairy & Eggs": {
        "butter",
        "cheese",
        "cottage cheese",
        "egg",
        "eggs",
        "milk",
        "yogurt",
    },
    "Grains & Bakery": {
        "bread",
        "brown rice",
        "oats",
        "pasta",
        "quinoa",
        "rice",
        "tortilla",
    },
    "Pantry": {
        "almond",
        "almonds",
        "beans",
        "chickpea",
        "chia",
        "flour",
        "granola",
        "honey",
        "lentil",
        "nut butter",
        "oil",
        "protein powder",
        "seed",
        "seeds",
        "seasoning",
        "spice",
        "vinegar",
    },
}

_GROCERY_NAME_ALIASES = {
    "berries": "mixed berries",
    "berry": "mixed berries",
    "carrot sticks": "carrot",
    "canned chickpeas": "chickpeas",
    "celery sticks": "celery",
    "cucumber slices": "cucumber",
    "eggs": "egg",
    "fresh spinach": "spinach",
    "firm tofu": "tofu",
    "rolled oats": "oats",
    "salmon fillet": "salmon",
    "scrambled eggs": "egg",
}

_GROCERY_DISPLAY_NAMES = {
    "egg": "Eggs",
    "mixed berries": "Mixed berries",
}

_MASS_TO_GRAMS = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592}
_VOLUME_TO_TBSP = {"tsp": 1 / 3, "tbsp": 1.0, "cup": 16.0}
_COUNT_UNITS = {
    "item",
    "piece",
    "slice",
    "medium",
    "large",
    "small",
    "clove",
    "leaf",
    "can",
    "scoop",
    "serving",
}
_PIECE_GRAMS = {
    "apple": 180,
    "avocado": 150,
    "banana": 120,
    "bell pepper": 150,
    "carrot": 60,
    "cucumber": 300,
    "sweet potato": 200,
    "tomato": 120,
}
_CUP_GRAMS = {
    "almonds": 143,
    "black beans": 172,
    "blueberries": 148,
    "brown rice": 195,
    "canned chickpeas": 164,
    "chickpeas": 164,
    "cherry tomatoes": 149,
    "cottage cheese": 226,
    "greek yogurt": 245,
    "hummus": 246,
    "mixed berries": 150,
    "quinoa": 185,
    "oats": 80,
    "spinach": 30,
}
_DEFAULT_PANTRY = {
    "cinnamon",
    "garlic powder",
    "olive oil",
    "paprika",
    "pepper",
    "salt",
    "sesame oil",
    "soy sauce",
    "spices cumin paprika chili powder",
}


def _grocery_name(value: str | None) -> tuple[str, str]:
    display = re.sub(r"\s+", " ", (value or "").strip())
    key = re.sub(r"[^a-z0-9]+", " ", display.lower()).strip()
    key = _GROCERY_NAME_ALIASES.get(key, key)
    display = _GROCERY_DISPLAY_NAMES.get(key, key.capitalize())
    return key, display


def _grocery_unit(value: str | None) -> str:
    unit = (value or "").strip().lower().rstrip(".")
    aliases = {
        "grams": "g",
        "gram": "g",
        "kilograms": "kg",
        "kilogram": "kg",
        "ounces": "oz",
        "ounce": "oz",
        "pounds": "lb",
        "pound": "lb",
        "tablespoons": "tbsp",
        "tablespoon": "tbsp",
        "teaspoons": "tsp",
        "teaspoon": "tsp",
        "cups": "cup",
        "servings": "serving",
        "pieces": "piece",
        "slices": "slice",
        "items": "item",
        "cloves": "clove",
        "leaves": "leaf",
        "cans": "can",
        "scoops": "scoop",
    }
    return aliases.get(unit, unit)


def _parse_grocery_number(value: str) -> float | None:
    value = value.strip()
    try:
        if " " in value and "/" in value:
            whole, fraction = value.split(None, 1)
            numerator, denominator = fraction.split("/", 1)
            return float(whole) + float(numerator) / float(denominator)
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator)
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def _grocery_measurement(qty: float | None, unit_value: str | None) -> tuple[str, float] | None:
    unit = _grocery_unit(unit_value)
    quantity = float(qty) if qty is not None else None
    if quantity is None:
        match = re.match(r"^(\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s+(.+)$", unit)
        if match:
            quantity = _parse_grocery_number(match.group(1))
            unit = _grocery_unit(match.group(2))
    if quantity is None:
        return None
    if unit in _MASS_TO_GRAMS:
        return "mass_g", quantity * _MASS_TO_GRAMS[unit]
    if unit in _VOLUME_TO_TBSP:
        return "volume_tbsp", quantity * _VOLUME_TO_TBSP[unit]
    if unit in _COUNT_UNITS or not unit:
        return "count", quantity
    return f"unit:{unit}", quantity


def _round_grocery(value: float) -> float:
    return round(value, 2) if value < 10 else round(value, 1)


def _format_grocery_measurements(name_key: str, measurements: dict[str, float], units: str | None) -> dict[str, Any]:
    values = dict(measurements)
    if "mass_g" in values and "count" in values and name_key in _PIECE_GRAMS:
        values["count"] += values.pop("mass_g") / _PIECE_GRAMS[name_key]
    if "mass_g" in values and "volume_tbsp" in values and name_key in _CUP_GRAMS:
        values["mass_g"] += values.pop("volume_tbsp") / 16 * _CUP_GRAMS[name_key]

    if len(values) != 1:
        summaries = []
        for dimension, value in sorted(values.items()):
            label = dimension.removeprefix("unit:").replace("mass_g", "g").replace("volume_tbsp", "tbsp")
            summaries.append(f"{_round_grocery(value):g} {label}")
        return {"quantity": None, "unit": "", "measurement_summary": " + ".join(summaries)}

    dimension, value = next(iter(values.items()))
    prefers_metric = str(units or "").strip().lower() == "metric"
    if dimension == "mass_g":
        if prefers_metric:
            if value >= 1000:
                return {"quantity": _round_grocery(value / 1000), "unit": "kg", "measurement_summary": None}
            return {"quantity": _round_grocery(value), "unit": "g", "measurement_summary": None}
        return {"quantity": _round_grocery(value / 28.3495), "unit": "oz", "measurement_summary": None}
    if dimension == "volume_tbsp":
        if value >= 8:
            return {"quantity": _round_grocery(value / 16), "unit": "cup", "measurement_summary": None}
        if value < 1:
            return {"quantity": _round_grocery(value * 3), "unit": "tsp", "measurement_summary": None}
        return {"quantity": _round_grocery(value), "unit": "tbsp", "measurement_summary": None}
    if dimension == "count":
        return {"quantity": ceil(value), "unit": "item", "measurement_summary": None}
    return {
        "quantity": _round_grocery(value),
        "unit": dimension.removeprefix("unit:"),
        "measurement_summary": None,
    }


def _grocery_category(name: str, meta: dict[str, Any] | None = None) -> str:
    explicit = str((meta or {}).get("category") or "").strip()
    if explicit and explicit.lower() != "other":
        return explicit
    lowered = name.lower()
    key = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    if key in _DEFAULT_PANTRY or "nut butter" in key or key in {"almond butter", "peanut butter"}:
        return "Pantry"
    for category, words in _GROCERY_CATEGORY_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words):
            return category
    return "Other"


class GroceryApprovalItemIn(BaseModel):
    id: str = Field(min_length=1, max_length=260)
    quantity: float | None = Field(default=None, ge=0)
    unit: str = Field(default="", max_length=32)
    pantry: bool = False


class GroceryApprovalIn(BaseModel):
    servings: int = Field(default=1, ge=1, le=12)
    items: list[GroceryApprovalItemIn] = Field(default_factory=list, max_length=300)


def _grocery_plan_fingerprint(plans: list[Plan]) -> list[dict[str, Any]]:
    return [{"grocery_schema": 2}] + [
        {
            "id": plan.id,
            "date": plan.date.isoformat(),
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        }
        for plan in plans
    ]


def _approval_to_dict(approval: GroceryApproval, current_fingerprint: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": approval.id,
        "start": approval.start_date.isoformat(),
        "end": approval.end_date.isoformat(),
        "servings": approval.servings,
        "items": approval.items or [],
        "approved_at": approval.approved_at.isoformat(),
        "stale": (approval.plan_fingerprint or []) != current_fingerprint,
    }


@router.get("/grocery-list/week", response_model=dict[str, Any])
def grocery_list_week(
    start: date_cls = Query(..., description="First date to include"),
    end: date_cls | None = Query(None, description="Last date to include (inclusive)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return a purchase-oriented, aggregated grocery list for up to 14 days."""
    final_day = end or (start + timedelta(days=6))
    if final_day < start:
        raise HTTPException(status_code=400, detail="End date must be on or after start date")
    if (final_day - start).days > 13:
        raise HTTPException(status_code=400, detail="Grocery lists can cover at most 14 days")

    plans = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.date >= start, Plan.date <= final_day)
        .order_by(Plan.date.asc())
        .all()
    )
    grouped: dict[str, dict[str, Any]] = {}
    for plan in plans:
        for meal, item in _iter_items(plan):
            name_key, display_name = _grocery_name(item.name)
            if not name_key or name_key == "water":
                continue
            entry = grouped.setdefault(
                name_key,
                {
                    "id": name_key,
                    "name": display_name,
                    "category": _grocery_category(display_name, item.meta),
                    "default_pantry": name_key in _DEFAULT_PANTRY,
                    "measurements": {},
                    "uses": [],
                },
            )
            measurement = _grocery_measurement(item.qty, item.unit)
            if measurement is not None:
                dimension, value = measurement
                entry["measurements"][dimension] = entry["measurements"].get(dimension, 0.0) + value
            use = {
                "date": plan.date.isoformat(),
                "meal_type": meal.meal_type,
                "meal_title": meal.title or meal.meal_type.title(),
            }
            if use not in entry["uses"]:
                entry["uses"].append(use)

    items = []
    for name_key, entry in grouped.items():
        measurements = entry.pop("measurements")
        entry.update(_format_grocery_measurements(name_key, measurements, user.units))
        items.append(entry)
    items.sort(key=lambda value: (value["category"], value["name"].lower()))

    expected_dates = [start + timedelta(days=offset) for offset in range((final_day - start).days + 1)]
    planned_dates = {plan.date for plan in plans}
    return {
        "start": start.isoformat(),
        "end": final_day.isoformat(),
        "plan_count": len(plans),
        "missing_dates": [day.isoformat() for day in expected_dates if day not in planned_dates],
        "items": items,
    }


@router.get("/grocery-list/approval", response_model=dict[str, Any])
def grocery_approval_status(
    start: date_cls = Query(...),
    end: date_cls | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    final_day = end or (start + timedelta(days=6))
    if final_day < start or (final_day - start).days > 13:
        raise HTTPException(status_code=400, detail="Approval range must be between 1 and 14 days")
    approval = (
        db.query(GroceryApproval)
        .filter(
            GroceryApproval.user_id == user.id,
            GroceryApproval.start_date == start,
            GroceryApproval.end_date == final_day,
        )
        .first()
    )
    if approval is None:
        return {"approval": None}
    plans = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.date >= start, Plan.date <= final_day)
        .order_by(Plan.date.asc())
        .all()
    )
    return {"approval": _approval_to_dict(approval, _grocery_plan_fingerprint(plans))}


@router.post("/grocery-list/approval", response_model=dict[str, Any])
def approve_grocery_list(
    payload: GroceryApprovalIn,
    start: date_cls = Query(...),
    end: date_cls | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    final_day = end or (start + timedelta(days=6))
    current = grocery_list_week(start=start, end=final_day, db=db, user=user)
    if current["missing_dates"]:
        raise HTTPException(status_code=400, detail="Every selected day needs a meal plan before approval")
    base_by_id = {item["id"]: item for item in current["items"]}
    supplied_by_id = {item.id: item for item in payload.items}
    unknown = sorted(set(supplied_by_id) - set(base_by_id))
    if unknown:
        raise HTTPException(status_code=400, detail="The grocery list changed. Refresh it before approving.")

    snapshot_items = []
    for item_id, base in base_by_id.items():
        supplied = supplied_by_id.get(item_id)
        base_quantity = base["quantity"]
        quantity = (
            supplied.quantity
            if supplied is not None
            else (round(base_quantity * payload.servings, 2) if base_quantity is not None else None)
        )
        snapshot_items.append(
            {
                **base,
                "quantity": quantity,
                "unit": _grocery_unit(supplied.unit) if supplied is not None else base["unit"],
                "pantry": supplied.pantry if supplied is not None else False,
            }
        )

    plans = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.date >= start, Plan.date <= final_day)
        .order_by(Plan.date.asc())
        .all()
    )
    fingerprint = _grocery_plan_fingerprint(plans)
    approval = (
        db.query(GroceryApproval)
        .filter(
            GroceryApproval.user_id == user.id,
            GroceryApproval.start_date == start,
            GroceryApproval.end_date == final_day,
        )
        .first()
    )
    now = datetime.utcnow()
    if approval is None:
        approval = GroceryApproval(user_id=user.id, start_date=start, end_date=final_day)
    approval.servings = payload.servings
    approval.items = snapshot_items
    approval.plan_fingerprint = fingerprint
    approval.approved_at = now
    approval.updated_at = now
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return {"approval": _approval_to_dict(approval, fingerprint)}


@router.get("/{date}/grocery.txt")
def grocery_txt(
    date: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = _parse_iso_date(date)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    lines: list[str] = []
    last_meal = None
    for m, it in _iter_items(plan):
        if last_meal != m.id:
            lines.append(f"# {m.meal_type.title()}: {m.title or ''}".strip())
            last_meal = m.id
        qty = f"{it.qty:g}" if isinstance(it.qty, (int, float)) else ""
        unit = it.unit or ""
        suffix = f" ({qty} {unit})".strip() if (qty or unit) else ""
        lines.append(f"- {it.name}{suffix}")
    txt = "\n".join(lines) + ("\n" if lines else "")
    return PlainTextResponse(txt, media_type="text/plain; charset=utf-8")


@router.get("/{date}/grocery.csv")
def grocery_csv(
    date: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = _parse_iso_date(date)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == day).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    import csv
    from io import StringIO

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "meal_type",
            "meal_title",
            "item",
            "qty",
            "unit",
            "kcal",
            "protein_g",
            "carbs_g",
            "fat_g",
        ]
    )
    for m, it in _iter_items(plan):
        w.writerow(
            [
                m.meal_type,
                m.title or "",
                it.name,
                it.qty if it.qty is not None else "",
                it.unit or "",
                it.kcal if it.kcal is not None else "",
                it.protein_g if it.protein_g is not None else "",
                it.carbs_g if it.carbs_g is not None else "",
                it.fat_g if it.fat_g is not None else "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="grocery-{day.isoformat()}.csv"'},
    )
