# app/routers/preferences.py — Profile diet + exclusions with real DB columns + logging (v2025-11-13d)
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import User
from app.routers.plan_models import UserPreference  # ORM mapped to user_preferences

logger = logging.getLogger(__name__)

router = APIRouter()  # included from main.py with some prefix

# ---------------------------
# Helpers
# ---------------------------

_ALLOWED_DIETS = {"omnivore", "pescatarian", "vegetarian", "vegan"}


def _normalize_diet(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if not s or s not in _ALLOWED_DIETS:
        return "omnivore"
    return s


def _coerce_list(val: Any) -> list[str]:
    """
    Normalize a field that might be:
      - a real list (['pescatarian'])
      - a JSON string ('["pescatarian"]')
      - a CSV string ('pescatarian, gluten_free')
    into a clean list of lowercased, stripped strings.
    """
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip().lower() for x in val if str(x).strip()]
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        # Try JSON first
        try:
            import json

            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip().lower() for x in parsed if str(x).strip()]
        except Exception:
            pass
        # Fallback: comma-separated
        return [p.strip().lower() for p in s.split(",") if p.strip()]
    # Unknown type → best effort
    s = str(val).strip().lower()
    return [s] if s else []


def _primary_diet_from_tags(tags: list[str]) -> str:
    """Derive a single diet radio button value from stored tags."""
    s = set(tags)
    if "vegan" in s:
        return "vegan"
    if "vegetarian" in s:
        return "vegetarian"
    if "pescatarian" in s:
        return "pescatarian"
    return "omnivore"


# ---------------------------
# Schemas
# ---------------------------


class PreferencesOut(BaseModel):
    diet: str  # 'omnivore'|'pescatarian'|'vegetarian'|'vegan'
    ingredient_exclusions: str  # comma-separated for the textbox


class PreferencesIn(BaseModel):
    diet: str | None = None
    ingredient_exclusions: str | None = None


# ---------------------------
# Internal serializer
# ---------------------------


def _to_out(pref: UserPreference | None) -> PreferencesOut:
    if not pref:
        # Default for new users
        logger.info("Preferences _to_out: no existing row, returning defaults")
        return PreferencesOut(diet="omnivore", ingredient_exclusions="")

    # 1) Prefer explicit diet_type column if present
    raw_diet = getattr(pref, "diet_type", None)
    diet = _normalize_diet(raw_diet)

    # 2) If diet_type is missing/blank, fall back to dietary_tags JSON
    if (not raw_diet) or diet == "omnivore":
        tags_raw = getattr(pref, "dietary_tags", None)
        tags = _coerce_list(tags_raw)
        if tags:
            diet = _primary_diet_from_tags(tags)

    # Exclusions: prefer plain TEXT column ingredient_exclusions,
    # but gracefully handle legacy JSON / list shapes via _coerce_list.
    raw_excl = getattr(pref, "ingredient_exclusions", None)
    excl_list = _coerce_list(raw_excl)
    excl_str = ", ".join(excl_list) if excl_list else ""

    logger.info(
        "Preferences _to_out: id=%s user_id=%s diet_type=%r dietary_tags=%r ingredient_exclusions_raw=%r -> diet=%s, excl=%s",
        getattr(pref, "id", None),
        getattr(pref, "user_id", None),
        raw_diet,
        getattr(pref, "dietary_tags", None),
        raw_excl,
        diet,
        excl_str,
    )

    return PreferencesOut(diet=diet, ingredient_exclusions=excl_str)


# ---------------------------
# Routes
# ---------------------------


@router.get("", response_model=PreferencesOut, tags=["preferences"])
@router.get("/preferences", response_model=PreferencesOut, tags=["preferences"])
def get_preferences(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Fetch current user's diet + ingredient exclusions.

    Matches either:
      • prefix="/v1"            →  GET /v1/preferences
      • prefix="/v1/preferences" → GET /v1/preferences
    """
    pref = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()

    logger.info(
        "Preferences GET: user_id=%s row=%r (diet_type=%r, dietary_tags=%r, ingredient_exclusions=%r)",
        user.id,
        getattr(pref, "id", None),
        getattr(pref, "diet_type", None),
        getattr(pref, "dietary_tags", None),
        getattr(pref, "ingredient_exclusions", None),
    )

    return _to_out(pref)


@router.put("", response_model=PreferencesOut, tags=["preferences"])
@router.put("/preferences", response_model=PreferencesOut, tags=["preferences"])
def upsert_preferences(
    payload: PreferencesIn = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upsert current user's diet + ingredient exclusions.

    Request body (from profile.js) looks like:
      { "diet": "pescatarian", "ingredient_exclusions": "mushrooms, shellfish" }
    """
    diet = _normalize_diet(payload.diet)
    excl_list = _coerce_list(payload.ingredient_exclusions or "")

    logger.info(
        "Preferences PUT incoming: user_id=%s raw_diet=%r normalized_diet=%s raw_exclusions=%r -> excl_list=%r",
        user.id,
        payload.diet,
        diet,
        payload.ingredient_exclusions,
        excl_list,
    )

    pref = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()

    now = datetime.utcnow()

    if not pref:
        pref = UserPreference(user_id=user.id)
        db.add(pref)
        db.flush()
        if hasattr(pref, "created_at"):
            pref.created_at = now
        logger.info(
            "Preferences PUT: created new row id=%s for user_id=%s",
            getattr(pref, "id", None),
            user.id,
        )

    # Persist canonical diet:
    #   - diet_type TEXT: 'omnivore'|'pescatarian'|'vegetarian'|'vegan'
    #   - dietary_tags JSON: [] or [diet] for non-omnivore
    if hasattr(pref, "diet_type"):
        pref.diet_type = diet
    if hasattr(pref, "dietary_tags"):
        pref.dietary_tags = [] if diet == "omnivore" else [diet]

    # Persist exclusions into TEXT column; we keep it simple CSV
    if hasattr(pref, "ingredient_exclusions"):
        pref.ingredient_exclusions = ", ".join(excl_list) if excl_list else ""

    if hasattr(pref, "updated_at"):
        pref.updated_at = now

    db.add(pref)
    db.commit()
    db.refresh(pref)

    logger.info(
        "Preferences PUT saved: id=%s user_id=%s diet_type=%r dietary_tags=%r ingredient_exclusions=%r",
        getattr(pref, "id", None),
        getattr(pref, "user_id", None),
        getattr(pref, "diet_type", None),
        getattr(pref, "dietary_tags", None),
        getattr(pref, "ingredient_exclusions", None),
    )

    return _to_out(pref)
