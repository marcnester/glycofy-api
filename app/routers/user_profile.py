# app/routers/user_profile.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import User

router = APIRouter(tags=["users"])


class UserMeUpdate(BaseModel):
    """
    Payload for updating the current user's profile.
    All fields are optional; only provided fields are updated.
    """

    display_name: str | None = None
    name: str | None = None
    units: str | None = None


def _apply_updates(user: User, payload: UserMeUpdate) -> bool:
    """
    Apply changes from payload onto the user model.
    Returns True if any field changed.
    """
    dirty = False

    # Normalize strings
    def norm(s: str | None) -> str | None:
        if s is None:
            return None
        s2 = s.strip()
        return s2 or None

    # display_name
    if payload.display_name is not None:
        new_name = norm(payload.display_name)
        if getattr(user, "display_name", None) != new_name:
            user.display_name = new_name
            dirty = True

    # name (if the column exists on your User model)
    if payload.name is not None and hasattr(user, "name"):
        new_name = norm(payload.name)
        if getattr(user, "name", None) != new_name:
            user.name = new_name
            dirty = True

    # units (optional)
    if payload.units is not None and hasattr(user, "units"):
        new_units = payload.units.strip().upper()
        if new_units not in ("US", "METRIC"):
            # keep it simple; if invalid, ignore silently
            new_units = getattr(user, "units", None) or "US"
        if getattr(user, "units", None) != new_units:
            user.units = new_units
            dirty = True

    return dirty


def _user_to_dict(user: User) -> dict:
    """
    Minimal shape for the frontend.
    This leaves your existing GET /users/me endpoint untouched;
    PATCH just returns something reasonable for debugging.
    """
    return {
        "id": user.id,
        "email": user.email,
        "display_name": getattr(user, "display_name", None),
        "name": getattr(user, "name", None),
        "units": getattr(user, "units", None),
    }


@router.patch("/users/me")
def update_me(
    payload: UserMeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    PATCH /users/me

    Allows the authenticated user to update:
      - display_name
      - name
      - units (US / METRIC)

    Any field omitted from the payload is left unchanged.
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    changed = _apply_updates(current_user, payload)
    if changed:
        db.add(current_user)
        db.commit()
        db.refresh(current_user)

    return _user_to_dict(current_user)
