from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PlannedWorkout, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/v1/training-events", tags=["training-events"])


class TrainingEventCreate(BaseModel):
    workout_date: date
    start_time: datetime | None = None
    sport: str = Field(min_length=1, max_length=32)
    duration_min: int = Field(ge=10, le=1440)
    intensity: Literal["easy", "moderate", "hard", "race"]
    distance_km: float | None = Field(default=None, ge=0, le=1000)
    priority: Literal["normal", "key"] = "normal"
    notes: str | None = Field(default=None, max_length=500)


class TrainingEventUpdate(BaseModel):
    workout_date: date | None = None
    start_time: datetime | None = None
    sport: str | None = Field(default=None, min_length=1, max_length=32)
    duration_min: int | None = Field(default=None, ge=10, le=1440)
    intensity: Literal["easy", "moderate", "hard", "race"] | None = None
    distance_km: float | None = Field(default=None, ge=0, le=1000)
    priority: Literal["normal", "key"] | None = None
    notes: str | None = Field(default=None, max_length=500)


def _event_dict(event: PlannedWorkout) -> dict:
    return {
        "id": event.id,
        "workout_date": event.workout_date.isoformat(),
        "start_time": f"{event.start_time.isoformat()}Z" if event.start_time else None,
        "sport": event.sport,
        "duration_min": event.duration_min,
        "intensity": event.intensity,
        "distance_km": event.distance_km,
        "priority": event.priority,
        "notes": event.notes,
        "source": event.source,
        "external_id": event.external_id,
    }


def _normalized_fields(payload: TrainingEventCreate | TrainingEventUpdate) -> dict:
    fields = payload.model_dump(exclude_unset=True)
    start_time = fields.get("start_time")
    if start_time and start_time.tzinfo:
        fields["start_time"] = start_time.astimezone(UTC).replace(tzinfo=None)
    return fields


@router.get("")
def list_training_events(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if date_to < date_from or date_to - date_from > timedelta(days=90):
        raise HTTPException(status_code=400, detail="Training-event range must be 0–90 days")
    events = (
        db.query(PlannedWorkout)
        .filter(
            PlannedWorkout.user_id == user.id,
            PlannedWorkout.workout_date >= date_from,
            PlannedWorkout.workout_date <= date_to,
        )
        .order_by(PlannedWorkout.workout_date, PlannedWorkout.start_time, PlannedWorkout.id)
        .all()
    )
    return {"items": [_event_dict(event) for event in events]}


@router.post("", status_code=201)
def create_training_event(
    payload: TrainingEventCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = PlannedWorkout(
        user_id=user.id,
        source="manual",
        **_normalized_fields(payload),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return _event_dict(event)


def _owned_manual_event(db: Session, user_id: int, event_id: int) -> PlannedWorkout:
    event = db.query(PlannedWorkout).filter(PlannedWorkout.id == event_id, PlannedWorkout.user_id == user_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Training event not found")
    if event.source != "manual":
        raise HTTPException(status_code=409, detail="Imported training events must be changed at their source")
    return event


@router.patch("/{event_id}")
def update_training_event(
    event_id: int,
    payload: TrainingEventUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = _owned_manual_event(db, user.id, event_id)
    for field, value in _normalized_fields(payload).items():
        setattr(event, field, value)
    db.commit()
    db.refresh(event)
    return _event_dict(event)


@router.delete("/{event_id}", status_code=204)
def delete_training_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = _owned_manual_event(db, user.id, event_id)
    db.delete(event)
    db.commit()
    return Response(status_code=204)
