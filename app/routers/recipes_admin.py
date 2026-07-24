# app/routers/recipes_admin.py
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import Recipe, User

router = APIRouter()


def _dev_only():
    if os.environ.get("ENV", "").lower() not in {"dev", "development", ""}:
        raise HTTPException(status_code=403, detail="Not available in this environment")


@router.post("/import")
def import_recipes(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    DEV-ONLY: bulk import recipes from a JSON payload:
    {
      "recipes":[
        {
          "title":"Oats & Yogurt",
          "meal_type":"breakfast",
          "diet_tags":"omnivore",
          "kcal":420, "protein_g":28, "carbs_g":58, "fat_g":10,
          "ingredients":"80g oats\n150g Greek yogurt\nberries",
          "instructions":"Mix and enjoy."
        },
        ...
      ]
    }
    """
    _dev_only()

    recs: list[dict[str, Any]] = payload.get("recipes") or []
    if not isinstance(recs, list):
        raise HTTPException(status_code=400, detail="recipes must be a list")

    created = 0
    for r in recs:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        meal_type = (r.get("meal_type") or "").strip().lower()
        if meal_type not in {"breakfast", "lunch", "dinner", "snack"}:
            continue

        obj = Recipe(
            title=title,
            meal_type=meal_type,
            diet_tags=r.get("diet_tags") or "omnivore",
            kcal=int(r.get("kcal") or 0),
            protein_g=int(r.get("protein_g") or 0),
            carbs_g=int(r.get("carbs_g") or 0),
            fat_g=int(r.get("fat_g") or 0),
            ingredients=r.get("ingredients") or "",
            instructions=r.get("instructions") or "",
        )
        db.add(obj)
        created += 1

    db.commit()
    return {"imported": created}
