from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db

# Import your model; adjust path if needed
try:
    from app.models import Recipe  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - keep mypy quiet if models shift
    Recipe = object  # type: ignore[misc,assignment]

router = APIRouter(prefix="/recipes", tags=["recipes"])


def _serialize_recipe(r: Any) -> dict[str, Any]:
    # Safe, attribute-tolerant serializer (no .to_dict() assumption)
    fields = ("id", "name", "kcal", "carbs_g", "protein_g", "fat_g")
    return {k: getattr(r, k, None) for k in fields}


@router.get("", summary="List recipes", response_model=list[dict[str, Any]])
def list_recipes(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        rows = db.query(Recipe).limit(100).all()  # type: ignore[attr-defined]
    except Exception:
        rows = []
    return [_serialize_recipe(r) for r in rows]
