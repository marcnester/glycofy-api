# app/routers/users.py
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text

from app.db import get_db
from app.models import User
from app.routers.auth import get_current_user

router = APIRouter()

# ---------- Schemas ----------


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    sex: str | None = None
    dob: date | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    diet_pref: str | None = None
    goal: str | None = None
    timezone: str | None = None


class UserUpdate(BaseModel):
    # all optional; only provided fields are updated
    sex: str | None = None
    dob: date | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    diet_pref: str | None = None
    goal: str | None = None
    timezone: str | None = None

    # Accept ISO date strings too (e.g., "1975-01-01")
    @field_validator("dob", mode="before")
    @classmethod
    def _normalize_dob(cls, v: Any) -> Any:
        if v in (None, "", "null"):
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)
            except ValueError:
                raise ValueError("dob must be YYYY-MM-DD")
        return v


# ---------- Helpers ----------

IMPACT_FIELDS = {"sex", "dob", "height_cm", "weight_kg", "diet_pref", "goal"}


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _impact_changed(user: User, payload: dict[str, Any]) -> bool:
    for k in IMPACT_FIELDS:
        if k in payload and getattr(user, k) != payload[k]:
            return True
    return False


def _invalidate_plans_from_today(db, user_id: int) -> int:
    """
    Delete plan rows from today forward.
    Works without importing ORM model by using raw SQL.
    If the table doesn't exist, it silently does nothing.
    """
    today = _today_iso()
    deleted = 0
    try:
        # First, check if the table day_plans exists (portable-ish)
        res = db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='day_plans'")).fetchone()
        if not res:
            # Table might have a different name; try a generic delete that will fail quietly
            return 0

        # Delete rows from today forward for this user
        del_res = db.execute(
            text("DELETE FROM day_plans WHERE user_id = :uid AND date >= :today"),
            {"uid": user_id, "today": today},
        )
        deleted = del_res.rowcount or 0
        return deleted
    except Exception as e:
        # Don’t break profile updates if plan invalidation fails
        print(f"[users._invalidate_plans_from_today] skipped due to: {e}")
        return 0


# ---------- Routes ----------


@router.get("/me", response_model=UserOut, summary="Get current user")
def get_me(current: User = Depends(get_current_user)) -> UserOut:
    if not current:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return current  # Pydantic v2 serializes from ORM object via from_attributes=True


@router.put("/me", response_model=UserOut, summary="Update current user profile")
def update_me(
    patch: UserUpdate,
    current: User = Depends(get_current_user),
    db=Depends(get_db),
) -> UserOut:
    if not current:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not payload:
        return current

    will_invalidate = _impact_changed(current, payload)

    # Apply updates
    for k, v in payload.items():
        setattr(current, k, v)

    db.add(current)
    db.commit()
    db.refresh(current)

    # Invalidate plans from today if impactful fields changed
    if will_invalidate:
        deleted = _invalidate_plans_from_today(db, current.id)
        db.commit()
        print(f"[users.update_me] invalidated {deleted} day_plans rows " f"for user={current.id} from { _today_iso() }")

    return current
