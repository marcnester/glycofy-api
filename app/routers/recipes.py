from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user, get_db
from app.models import Recipe, User

router = APIRouter()

@router.get("", response_model=List[dict])
def list_recipes(
    diet: Optional[str] = Query(None, description="omnivore|pescatarian|vegan"),
    meal_type: Optional[str] = Query(None, description="breakfast|lunch|dinner|snack"),
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    q = db.query(Recipe)
    if diet:
        diet = diet.lower()
        q = q.filter(Recipe.diet_tags.ilike(f"%{diet}%"))
    if meal_type:
        meal_type = meal_type.lower()
        q = q.filter(Recipe.meal_type == meal_type)
    rows = q.order_by(Recipe.title.asc()).all()
    return [r.to_dict() for r in rows]

@router.get("/{rid}", response_model=dict)
def get_recipe(
    rid: int,
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
):
    r = db.query(Recipe).get(rid)
    if not r:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return r.to_dict()