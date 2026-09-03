# app/routers/users.py
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Activity,
    EnergyTarget,
    GroceryApproval,
    GroceryPreference,
    MealFeedback,
    OAuthAccount,
    Plan,
    PlannedWorkout,
    User,
    UserPreference,
    WeeklyPlanningJob,
)
from app.observability import record_security_event

# IMPORTANT: use the cookie-aware get_current_user from routers.auth
from app.routers.auth import _clear_all_session_cookies, get_current_user

router = APIRouter(prefix="/users", tags=["users"])


# ---------- Helpers ----------
def _infer_name(user: User) -> str | None:
    """
    Prefer a real display name if present. If the column doesn't exist,
    getattr(...) will just return None. As a last resort, derive something
    readable from the email local-part.
    """
    # NEW: also check full_name and name in order
    name = getattr(user, "display_name", None) or getattr(user, "full_name", None) or getattr(user, "name", None)
    if name:
        return name

    # Fallback: derive from email ("marc.nester" -> "Marc Nester")
    try:
        local = (user.email or "").split("@", 1)[0]
        if not local:
            return None
        parts = [p for p in local.replace(".", " ").replace("_", " ").split(" ") if p]
        if not parts:
            return None
        return " ".join(s[:1].upper() + s[1:] for s in parts)
    except Exception:
        return None


def _norm_name(val: str | None) -> str | None:
    if val is None:
        return None
    s = val.strip()
    return s or None


# ---------- Schemas ----------
class UserOut(BaseModel):
    id: int
    email: str
    # explicitly surface display_name (if your DB has it)
    display_name: str | None = None
    # friendly name the UI expects (prefers display_name/_infer_name)
    name: str | None = None

    sex: str | None = None
    dob: date | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    diet_pref: str | None = None
    goal: str | None = None
    timezone: str | None = None
    # also surface units (used elsewhere in the UI)
    units: str | None = None
    email_verified: bool = False

    class Config:
        from_attributes = True  # pydantic v2: map from ORM


class UserUpdate(BaseModel):
    # NEW: allow editing the name from the Profile page
    display_name: str | None = Field(None, max_length=128)
    name: str | None = Field(None, max_length=128)

    sex: Literal["male", "female", "unspecified"] | None = None
    dob: date | None = None
    height_cm: float | None = Field(None, ge=100, le=250)
    weight_kg: float | None = Field(None, ge=30, le=400)
    diet_pref: str | None = Field(None, max_length=32)
    goal: Literal["performance", "maintain", "lose", "gain"] | None = None
    timezone: str | None = Field(None, max_length=64)
    units: Literal["US", "Metric"] | None = None

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, value: date | None) -> date | None:
        if value is None:
            return value
        today = date.today()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if age < 13 or age > 120:
            raise ValueError("Age must be between 13 and 120")
        return value


class DeleteAccountRequest(BaseModel):
    confirmation: str


# ---------- Routes ----------
@router.get("/me", response_model=UserOut)
def get_me(
    user: User = Depends(get_current_user),
) -> UserOut:
    """
    Return the authenticated user's profile (supports Bearer OR access_token/glyco_* cookies).
    We also compute a friendly `name` expected by the UI, preferring any of:
      display_name -> full_name -> name -> email-derived.
    """
    display_name = getattr(user, "display_name", None)
    name = _infer_name(user)
    units = getattr(user, "units", None)

    return UserOut.model_validate(
        {
            "id": user.id,
            "email": user.email,
            "display_name": display_name,
            "name": name,
            "sex": user.sex,
            "dob": user.dob,
            "height_cm": user.height_cm,
            "weight_kg": user.weight_kg,
            "diet_pref": user.diet_pref,
            "goal": user.goal,
            "timezone": user.timezone,
            "units": units,
            "email_verified": bool(user.email_verified_at),
        }
    )


@router.put("/me", response_model=UserOut)
def update_me(
    body: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserOut:
    """
    Update authenticated user's profile preferences. Only provided fields are changed.

    For the name:
      - We accept either `display_name` or `name` in the payload.
      - We normalize it and then write it into every name-like column that
        actually exists on the User model:
        display_name, full_name, name.
    """
    changed = False

    # ---- name fields (NEW) ----
    new_name_val: str | None = None
    if body.display_name is not None:
        new_name_val = _norm_name(body.display_name)
    elif body.name is not None:
        new_name_val = _norm_name(body.name)

    if new_name_val is not None:
        # update any of these attributes that exist on the model
        for attr in ("display_name", "full_name", "name"):
            if hasattr(user, attr):
                if getattr(user, attr, None) != new_name_val:
                    setattr(user, attr, new_name_val)
                    changed = True

    # ---- existing profile fields ----
    if body.sex is not None and body.sex != user.sex:
        user.sex = body.sex
        changed = True
    if body.dob is not None and body.dob != user.dob:
        user.dob = body.dob
        changed = True
    if body.height_cm is not None and body.height_cm != user.height_cm:
        user.height_cm = float(body.height_cm)
        changed = True
    if body.weight_kg is not None and body.weight_kg != user.weight_kg:
        user.weight_kg = float(body.weight_kg)
        changed = True
    if body.diet_pref is not None and body.diet_pref != user.diet_pref:
        user.diet_pref = body.diet_pref
        changed = True
    if body.goal is not None and body.goal != user.goal:
        user.goal = body.goal
        changed = True
    if body.timezone is not None and body.timezone != user.timezone:
        user.timezone = body.timezone
        changed = True
    if body.units is not None and body.units != user.units:
        user.units = body.units
        changed = True

    if changed:
        db.add(user)
        db.commit()
        db.refresh(user)

    # mirror GET /me shape (including computed name/display_name)
    display_name = getattr(user, "display_name", None)
    name = _infer_name(user)
    units = getattr(user, "units", None)

    return UserOut.model_validate(
        {
            "id": user.id,
            "email": user.email,
            "display_name": display_name,
            "name": name,
            "sex": user.sex,
            "dob": user.dob,
            "height_cm": user.height_cm,
            "weight_kg": user.weight_kg,
            "diet_pref": user.diet_pref,
            "goal": user.goal,
            "timezone": user.timezone,
            "units": units,
            "email_verified": bool(user.email_verified_at),
        }
    )


def _public_columns(row, excluded: set[str] | None = None) -> dict:
    blocked = excluded or set()
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in blocked}


@router.get("/me/export")
def export_my_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plans = db.query(Plan).filter(Plan.user_id == user.id).order_by(Plan.date.asc()).all()
    data = {
        "exported_at": date.today().isoformat(),
        "account": _public_columns(user, {"password_hash", "token_version"}),
        "connected_services": [
            {
                "provider": row.provider,
                "external_athlete_id": row.external_athlete_id,
                "scope": row.scope,
                "created_at": row.created_at,
            }
            for row in db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id).all()
        ],
        "activities": [_public_columns(row) for row in db.query(Activity).filter(Activity.user_id == user.id).all()],
        "planned_workouts": [
            _public_columns(row) for row in db.query(PlannedWorkout).filter(PlannedWorkout.user_id == user.id).all()
        ],
        "meal_preferences": [
            _public_columns(row) for row in db.query(UserPreference).filter(UserPreference.user_id == user.id).all()
        ],
        "energy_targets": [
            _public_columns(row) for row in db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id).all()
        ],
        "plans": [
            {
                **_public_columns(plan),
                "meals": [
                    {**_public_columns(meal), "items": [_public_columns(item) for item in meal.items]}
                    for meal in plan.meals
                ],
            }
            for plan in plans
        ],
        "meal_feedback": [
            _public_columns(row) for row in db.query(MealFeedback).filter(MealFeedback.user_id == user.id).all()
        ],
        "grocery_preferences": [
            _public_columns(row)
            for row in db.query(GroceryPreference).filter(GroceryPreference.user_id == user.id).all()
        ],
        "grocery_approvals": [
            _public_columns(row) for row in db.query(GroceryApproval).filter(GroceryApproval.user_id == user.id).all()
        ],
        "weekly_planning_jobs": [
            _public_columns(row, {"payload", "result", "error"})
            for row in db.query(WeeklyPlanningJob).filter(WeeklyPlanningJob.user_id == user.id).all()
        ],
    }
    response = JSONResponse(jsonable_encoder(data))
    response.headers["Content-Disposition"] = 'attachment; filename="glycofy-data-export.json"'
    return response


@router.delete("/me")
def delete_my_account(
    request: Request,
    body: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.confirmation.strip() != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm account deletion")
    user_id = user.id
    from app.routers.oauth_strava import revoke_strava_for_user

    provider_revoked = revoke_strava_for_user(db, user_id)
    record_security_event(db, request, "account_deletion", "requested", severity="warning", user_id=user_id)
    db.delete(user)
    db.commit()
    response = JSONResponse({"ok": True, "strava_revoked": provider_revoked})
    _clear_all_session_cookies(response)
    return response
