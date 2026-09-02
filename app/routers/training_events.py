from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Activity, PlannedWorkout, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/v1/training-events", tags=["training-events"])
_CSV_MAX_BYTES = 1_000_000


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


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _first(row: dict[str, str], *names: str) -> str:
    normalized = {_header(key): (value or "").strip() for key, value in row.items()}
    return next((normalized[_header(name)] for name in names if normalized.get(_header(name))), "")


def _csv_date(value: str) -> date:
    clean = value.strip().split("T", 1)[0].split(" ", 1)[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    raise ValueError("unrecognized workout date")


def _csv_duration_minutes(row: dict[str, str]) -> int:
    minutes = _first(row, "DurationMinutes", "PlannedDurationMinutes")
    if minutes:
        return round(float(minutes))
    value = _first(row, "PlannedDuration", "Duration", "TotalTime")
    if not value:
        raise ValueError("missing planned duration")
    if ":" in value:
        parts = [float(part) for part in value.split(":")]
        if len(parts) == 3:
            return round(parts[0] * 60 + parts[1] + parts[2] / 60)
        if len(parts) == 2:
            return round(parts[0] * 60 + parts[1])
    # TrainingPeaks PlannedDuration is expressed in decimal hours.
    return round(float(value) * 60)


def _csv_intensity(row: dict[str, str], title: str) -> str:
    raw = _first(row, "Intensity", "PlannedIntensity", "IntensityFactor", "IF")
    lowered = raw.lower()
    if lowered in {"easy", "moderate", "hard", "race"}:
        return lowered
    try:
        factor = float(raw)
        if factor > 1.5:
            factor /= 100.0
        return "easy" if factor < 0.7 else "moderate" if factor < 0.85 else "hard" if factor < 1.0 else "race"
    except ValueError:
        words = title.lower()
        if any(token in words for token in ("race", "event", "competition")):
            return "race"
        if any(token in words for token in ("interval", "threshold", "tempo", "vo2", "hard")):
            return "hard"
        if any(token in words for token in ("recovery", "easy", "rest")):
            return "easy"
        return "moderate"


def _csv_start_time(row: dict[str, str], workout_date: date) -> datetime | None:
    raw = _first(row, "StartTimePlanned", "PlannedStartTime", "StartTime", "ScheduledTime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
            try:
                parsed_time = datetime.strptime(raw, fmt).time()
                return datetime.combine(workout_date, parsed_time)
            except ValueError:
                continue
    return None


def _parse_trainingpeaks_csv(contents: bytes, today: date | None = None) -> tuple[list[dict], list[str], int]:
    if len(contents) > _CSV_MAX_BYTES:
        raise HTTPException(status_code=413, detail="CSV must be 1 MB or smaller")
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must use UTF-8 encoding") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header row is missing")
    rows: list[dict] = []
    errors: list[str] = []
    skipped_past = 0
    current_day = today or datetime.now(UTC).date()
    for line_number, row in enumerate(reader, start=2):
        try:
            workout_date = _csv_date(_first(row, "WorkoutDay", "WorkoutDate", "PlannedDate", "Date"))
            if workout_date < current_day:
                skipped_past += 1
                continue
            duration_min = _csv_duration_minutes(row)
            if not 10 <= duration_min <= 1440:
                raise ValueError("duration must be 10–1440 minutes")
            sport = _first(row, "WorkoutType", "ActivityType", "Sport", "Type") or "Other"
            sport = {"bike": "Ride", "biking": "Ride", "cycling": "Ride"}.get(sport.lower(), sport.title())[:32]
            title = _first(row, "WorkoutTitle", "Title", "WorkoutName", "Name") or f"{sport} workout"
            description = _first(row, "WorkoutDescription", "Description", "Notes")
            distance_km = None
            meters_raw = _first(row, "DistanceInMeters")
            distance_raw = meters_raw or _first(row, "DistancePlanned", "PlannedDistance", "DistanceKm")
            if distance_raw:
                distance = float(distance_raw)
                unit = _first(row, "PlannedDistanceUnit", "DistanceUnit").lower()
                if meters_raw or "meter" in unit:
                    distance /= 1000.0
                elif "mile" in unit:
                    distance *= 1.609344
                distance_km = round(distance, 2)
            fingerprint = "|".join((workout_date.isoformat(), title, sport, str(duration_min)))
            intensity = _csv_intensity(row, title)
            rows.append(
                {
                    "workout_date": workout_date,
                    "start_time": _csv_start_time(row, workout_date),
                    "sport": sport,
                    "duration_min": duration_min,
                    "intensity": intensity,
                    "distance_km": distance_km,
                    "priority": "key" if intensity == "race" else "normal",
                    "notes": " — ".join(part for part in (title, description) if part)[:500],
                    "source": "trainingpeaks_csv",
                    "external_id": "tp-csv:" + hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
                }
            )
        except (TypeError, ValueError) as exc:
            if len(errors) < 25:
                errors.append(f"Row {line_number}: {exc}")
    return rows, errors, skipped_past


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


@router.get("/context/{plan_date}")
def training_context(
    plan_date: date,
    days: int = Query(7, ge=1, le=7),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Describe how much real training context is available to meal planning."""
    now = datetime.now(UTC).replace(tzinfo=None)
    recent_start = now - timedelta(days=3)
    recent = (
        db.query(Activity)
        .filter(
            Activity.user_id == user.id,
            Activity.start_time >= recent_start,
            Activity.start_time <= now,
        )
        .count()
    )
    range_end = plan_date + timedelta(days=days - 1)
    planned = (
        db.query(PlannedWorkout)
        .filter(
            PlannedWorkout.user_id == user.id,
            PlannedWorkout.workout_date >= plan_date,
            PlannedWorkout.workout_date <= range_end,
        )
        .count()
    )
    if recent and planned:
        state = "complete"
        title = "Training-aware meal planning is ready"
        message = (
            f"Glycofy can use {recent} recent completed workout{'s' if recent != 1 else ''} for recovery and "
            f"{planned} upcoming workout{'s' if planned != 1 else ''} for fueling over this period."
        )
    elif recent:
        state = "missing_future"
        title = "Upcoming training is missing"
        message = (
            "Recent completed training will inform recovery, but no upcoming workouts are scheduled for this period. "
            "Pre-workout fueling will use your athlete profile and standard carbohydrate, protein, fat, and calorie targets."
        )
    elif planned:
        state = "missing_recent"
        title = "Recent training is missing"
        message = (
            "Upcoming workouts will inform fueling, but no completed training is available from the last three days. "
            "Recovery will use your athlete profile and standard carbohydrate, protein, fat, and calorie targets."
        )
    else:
        state = "standard"
        title = "Planning with standard athlete targets"
        message = (
            "No recent or upcoming training data is available. This plan will use your athlete profile and standard "
            "carbohydrate, protein, fat, and calorie targets. Add training for recovery- and workout-specific fueling."
        )
    return {
        "state": state,
        "title": title,
        "message": message,
        "recent_completed": recent,
        "upcoming_planned": planned,
        "from": plan_date.isoformat(),
        "to": range_end.isoformat(),
    }


@router.post("/import/trainingpeaks")
async def import_trainingpeaks_csv(
    file: UploadFile = File(...),
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Choose a TrainingPeaks CSV file")
    rows, errors, skipped_past = _parse_trainingpeaks_csv(await file.read())
    unique_rows = {row["external_id"]: row for row in rows}
    preview = [
        {
            **row,
            "workout_date": row["workout_date"].isoformat(),
            "start_time": f'{row["start_time"].isoformat()}Z' if row["start_time"] else None,
        }
        for row in list(unique_rows.values())[:100]
    ]
    result = {
        "valid": len(unique_rows),
        "invalid": len(errors),
        "skipped_past": skipped_past,
        "errors": errors,
        "preview": preview,
        "confirmed": confirm,
    }
    if not confirm:
        return result

    imported = updated = unchanged = 0
    for external_id, fields in unique_rows.items():
        event = (
            db.query(PlannedWorkout)
            .filter(
                PlannedWorkout.user_id == user.id,
                PlannedWorkout.source == "trainingpeaks_csv",
                PlannedWorkout.external_id == external_id,
            )
            .first()
        )
        if event is None:
            db.add(PlannedWorkout(user_id=user.id, **fields))
            imported += 1
            continue
        changed = False
        for field, value in fields.items():
            if getattr(event, field) != value:
                setattr(event, field, value)
                changed = True
        if changed:
            updated += 1
        else:
            unchanged += 1
    db.commit()
    return {**result, "imported": imported, "updated": updated, "unchanged": unchanged}


def _owned_editable_event(db: Session, user_id: int, event_id: int) -> PlannedWorkout:
    event = db.query(PlannedWorkout).filter(PlannedWorkout.id == event_id, PlannedWorkout.user_id == user_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Training event not found")
    if event.source not in {"manual", "trainingpeaks_csv"}:
        raise HTTPException(status_code=409, detail="Imported training events must be changed at their source")
    return event


@router.patch("/{event_id}")
def update_training_event(
    event_id: int,
    payload: TrainingEventUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    event = _owned_editable_event(db, user.id, event_id)
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
    event = _owned_editable_event(db, user.id, event_id)
    db.delete(event)
    db.commit()
    return Response(status_code=204)
