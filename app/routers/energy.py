# app/routers/energy.py
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import User
from app.routers.plan_models import EnergyTarget  # uses same model as plans.py

router = APIRouter()

# ---------------------------
# Helpers
# ---------------------------


def _parse_iso_date(d: str) -> date_cls:
    try:
        return date_cls.fromisoformat(d)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Bad date '{d}', expected YYYY-MM-DD")


def _energy_to_dict(e: EnergyTarget) -> dict[str, Any]:
    return {
        "user_id": e.user_id,
        "date": e.date.isoformat(),
        "tdee_kcal": e.tdee_kcal,
        "training_kcal": e.training_kcal,
        "target_kcal": e.target_kcal,
        "protein_g": e.protein_g,
        "carbs_g": e.carbs_g,
        "fat_g": e.fat_g,
        "meta": e.meta or {},
        "created_at": (e.created_at or datetime.utcnow()).isoformat(),
        "updated_at": (e.updated_at or datetime.utcnow()).isoformat(),
    }


# ---------------------------
# Schemas
# ---------------------------


class EnergyUpsertIn(BaseModel):
    tdee_kcal: float | None = Field(default=None, ge=0)
    training_kcal: float | None = Field(default=None, ge=0)
    target_kcal: float | None = Field(default=None, ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    carbs_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------
# Endpoints
# ---------------------------


@router.get("/{date}")
def get_energy_for_day(
    date: str = Path(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = _parse_iso_date(date)
    row: EnergyTarget | None = (
        db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
    )
    if not row:
        # return empty “not set” shape instead of 404 to make UI simpler
        return {
            "user_id": user.id,
            "date": day.isoformat(),
            "tdee_kcal": None,
            "training_kcal": None,
            "target_kcal": None,
            "protein_g": None,
            "carbs_g": None,
            "fat_g": None,
            "meta": {},
            "created_at": None,
            "updated_at": None,
        }
    return _energy_to_dict(row)


@router.put("/{date}")
def upsert_energy_for_day(
    date: str = Path(..., description="YYYY-MM-DD"),
    payload: EnergyUpsertIn = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Idempotent upsert:
      - If a row exists for (user_id, date), update fields & updated_at
      - Else create with created_at & updated_at set
    """
    day = _parse_iso_date(date)
    row: EnergyTarget | None = (
        db.query(EnergyTarget).filter(EnergyTarget.user_id == user.id, EnergyTarget.date == day).first()
    )

    now = datetime.utcnow()

    if row is None:
        row = EnergyTarget(
            user_id=user.id,
            date=day,
            tdee_kcal=payload.tdee_kcal,
            training_kcal=payload.training_kcal,
            target_kcal=payload.target_kcal,
            protein_g=payload.protein_g,
            carbs_g=payload.carbs_g,
            fat_g=payload.fat_g,
            meta=payload.meta or {},
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        # Only overwrite keys that are provided (allow partial updates)
        if payload.tdee_kcal is not None:
            row.tdee_kcal = payload.tdee_kcal
        if payload.training_kcal is not None:
            row.training_kcal = payload.training_kcal
        if payload.target_kcal is not None:
            row.target_kcal = payload.target_kcal
        if payload.protein_g is not None:
            row.protein_g = payload.protein_g
        if payload.carbs_g is not None:
            row.carbs_g = payload.carbs_g
        if payload.fat_g is not None:
            row.fat_g = payload.fat_g
        if payload.meta is not None:
            row.meta = payload.meta
        row.updated_at = now
        db.add(row)

    db.commit()
    db.refresh(row)
    return _energy_to_dict(row)
