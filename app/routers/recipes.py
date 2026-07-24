# app/routers/recipes.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import Recipe, User

router = APIRouter()

# ----------------------------
# Helpers
# ----------------------------


def _to_iso(dt: Any) -> str | None:
    """Return ISO string if dt is datetime or an ISO-like string; else None."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    if isinstance(dt, str):
        # best-effort: return as-is (DB might store text timestamps)
        return dt
    return None


def _recipe_to_dict(r: Recipe) -> dict[str, Any]:
    # Some DBs/old schemas may not have created_at; use getattr defensively.
    created_at_val = _to_iso(getattr(r, "created_at", None))
    return {
        "id": r.id,
        "title": r.title,
        "meal_type": r.meal_type,
        "diet_tags": r.diet_tags,
        "kcal": r.kcal,
        "protein_g": r.protein_g,
        "carbs_g": r.carbs_g,
        "fat_g": r.fat_g,
        "ingredients": r.ingredients,
        "instructions": r.instructions,
        "created_at": created_at_val,
    }


# ----------------------------
# Endpoints
# ----------------------------


@router.get("/", response_model=dict[str, Any])
def list_recipes(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=250),
    q: str | None = Query(None, description="search in title"),
    meal_type: str | None = Query(None, description="breakfast|lunch|dinner|snack"),
    diet: str | None = Query(None, description="substring match in diet_tags"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Browse recipes with simple filters and pagination.
    """
    qry = db.query(Recipe)

    if q:
        qry = qry.filter(Recipe.title.ilike(f"%{q.strip()}%"))

    if meal_type:
        qry = qry.filter(Recipe.meal_type == meal_type.strip().lower())

    if diet:
        qry = qry.filter(Recipe.diet_tags.ilike(f"%{diet.strip().lower()}%"))

    total = qry.count()
    items: list[Recipe] = qry.order_by(Recipe.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_recipe_to_dict(r) for r in items],
    }


@router.get("/{recipe_id}", response_model=dict[str, Any])
def get_recipe(
    recipe_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Fetch a single recipe by id.
    """
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return _recipe_to_dict(r)
