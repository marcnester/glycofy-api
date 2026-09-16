from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import MealFeedback, MealPreferenceEvent, Plan, PlanMeal, User
from app.services.meal_feedback import feedback_context, meal_preference_features

router = APIRouter()


class MealFeedbackIn(BaseModel):
    outcome: Literal["eaten", "substituted", "skipped"]
    portion: Literal["too_small", "right", "too_large"] | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    hunger_after: Literal["hungry", "satisfied", "too_full"] | None = None
    energy_after: Literal["low", "steady", "great"] | None = None
    digestion: Literal["comfortable", "minor_issue", "poor"] | None = None
    practicality: Literal["easy", "manageable", "difficult"] | None = None
    note: str | None = Field(default=None, max_length=500)


class MealPreferenceIn(BaseModel):
    signal: Literal["love", "repeat", "avoid", "swap"]


def _owned_meal(db: Session, meal_id: int, user_id: int) -> tuple[PlanMeal, Plan]:
    row = (
        db.query(PlanMeal, Plan)
        .join(Plan, Plan.id == PlanMeal.plan_id)
        .filter(PlanMeal.id == meal_id, Plan.user_id == user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    return row


def _serialize(row: MealFeedback) -> dict:
    return {
        "meal_id": row.plan_meal_id,
        "plan_date": row.plan_date.isoformat(),
        "meal_type": row.meal_type,
        "meal_title": row.meal_title,
        "outcome": row.outcome,
        "portion": row.portion,
        "rating": row.rating,
        "hunger_after": row.hunger_after,
        "energy_after": row.energy_after,
        "digestion": row.digestion,
        "practicality": row.practicality,
        "note": row.note,
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/meals/{meal_id}")
def get_meal_feedback(meal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owned_meal(db, meal_id, user.id)
    row = db.query(MealFeedback).filter_by(user_id=user.id, plan_meal_id=meal_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return _serialize(row)


@router.put("/meals/{meal_id}")
def save_meal_feedback(
    meal_id: int,
    payload: MealFeedbackIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meal, plan = _owned_meal(db, meal_id, user.id)
    row = db.query(MealFeedback).filter_by(user_id=user.id, plan_meal_id=meal_id).first()
    now = datetime.utcnow()
    if row is None:
        row = MealFeedback(
            user_id=user.id,
            plan_meal_id=meal.id,
            plan_date=plan.date,
            meal_type=meal.meal_type,
            meal_title=(meal.title or meal.meal_type.title())[:160],
            outcome=payload.outcome,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    for field, value in payload.model_dump().items():
        setattr(row, field, value.strip() if field == "note" and value else value)
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.delete("/meals/{meal_id}", status_code=204)
def delete_meal_feedback(
    meal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_meal(db, meal_id, user.id)
    row = db.query(MealFeedback).filter_by(user_id=user.id, plan_meal_id=meal_id).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=204)


@router.get("/insights")
def get_feedback_insights(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return feedback_context(db, user.id)


@router.put("/meals/{meal_id}/preference")
def save_meal_preference(
    meal_id: int,
    payload: MealPreferenceIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    meal, plan = _owned_meal(db, meal_id, user.id)
    source = "implicit" if payload.signal == "swap" else "explicit"
    if source == "explicit":
        db.query(MealPreferenceEvent).filter(
            MealPreferenceEvent.user_id == user.id,
            MealPreferenceEvent.plan_meal_id == meal.id,
            MealPreferenceEvent.source == "explicit",
        ).delete(synchronize_session=False)
    elif (
        db.query(MealPreferenceEvent)
        .filter_by(user_id=user.id, plan_meal_id=meal.id, signal="swap", source="implicit")
        .first()
    ):
        return {"meal_id": meal.id, "signal": "swap", "saved": True}

    row = MealPreferenceEvent(
        user_id=user.id,
        plan_meal_id=meal.id,
        plan_date=plan.date,
        meal_type=meal.meal_type,
        meal_title=(meal.title or meal.meal_type.title())[:160],
        signal=payload.signal,
        source=source,
        features=meal_preference_features(meal),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return {"meal_id": meal.id, "signal": row.signal, "saved": True}


@router.get("/preferences")
def get_learned_preferences(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return feedback_context(db, user.id)


@router.delete("/preferences", status_code=204)
def reset_learned_preferences(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(MealPreferenceEvent).filter(MealPreferenceEvent.user_id == user.id).delete(synchronize_session=False)
    db.query(MealFeedback).filter(MealFeedback.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=204)
