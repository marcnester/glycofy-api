# app/routers/activities.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import Activity, User

router = APIRouter()


# ---------- helpers -----------------------------------------------------------


def _to_dict(a: Activity) -> dict[str, Any]:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "sport": a.sport,
        "start_time": (a.start_time.isoformat() if a.start_time else None),
        "duration_s": int(a.duration_s or 0),
        "kcal": int(a.kcal or 0),
        "distance_m": float(a.distance_m or 0.0),
        "source_provider": a.source_provider,
        "source_id": a.source_id,
        "created_at": (a.created_at.isoformat() if a.created_at else None),
    }


def _parse_date_opt(s: str | None) -> datetime | None:
    if not s:
        return None
    # Accept YYYY-MM-DD or full ISO8601
    try:
        if len(s) == 10:
            d = datetime.strptime(s, "%Y-%m-%d")
            return d
        # fallback parse
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date: {s}")


# ---------- routes (mounted under /activities) --------------------------------


@router.get("", summary="List activities (paginated)")
def list_activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500),
    from_: str | None = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    sport: str | None = Query(None, description="Filter by sport"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    q = db.query(Activity).filter(Activity.user_id == current_user.id)

    start_dt = _parse_date_opt(from_)
    end_dt = _parse_date_opt(to)
    if start_dt:
        q = q.filter(Activity.start_time >= start_dt)
    if end_dt:
        # make 'to' inclusive by adding 1 day if only date given
        if len(to or "") == 10:
            end_dt = end_dt + timedelta(days=1)
        q = q.filter(Activity.start_time < end_dt)

    if sport:
        q = q.filter(Activity.sport == sport)

    total = q.count()
    items = q.order_by(Activity.start_time.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_to_dict(a) for a in items],
    }


@router.post("", summary="Create an activity (manual entry)")
def create_activity(
    payload: dict[str, Any] = Body(
        ...,
        example={
            "sport": "cycling",
            "start_time": "2025-10-16T07:08:51Z",
            "duration_s": 3600,
            "kcal": 600,
            "distance_m": 30000,
        },
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    # Basic validation
    sport = (payload.get("sport") or "").lower()
    if not sport:
        raise HTTPException(status_code=400, detail="sport is required")

    st_raw = payload.get("start_time")
    if not st_raw:
        raise HTTPException(status_code=400, detail="start_time is required")
    try:
        start_time = datetime.fromisoformat(str(st_raw).replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail="start_time must be ISO8601")

    duration_s = int(payload.get("duration_s") or 0)
    kcal = int(payload.get("kcal") or 0)
    distance_m = float(payload.get("distance_m") or 0.0)

    a = Activity(
        user_id=current_user.id,
        sport=sport,
        start_time=start_time,
        duration_s=duration_s,
        kcal=kcal,
        distance_m=distance_m,
        source_provider=payload.get("source_provider"),
        source_id=payload.get("source_id"),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_dict(a)


@router.get("/today", summary="Today’s activities (local day approximation)")
def today_activities(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    # Use server local date for MVP; could be improved with user.timezone
    now = datetime.now()
    day_start = datetime(now.year, now.month, now.day)
    day_end = day_start + timedelta(days=1)

    items = (
        db.query(Activity)
        .filter(Activity.user_id == current_user.id)
        .filter(Activity.start_time >= day_start)
        .filter(Activity.start_time < day_end)
        .order_by(Activity.start_time.asc())
        .all()
    )
    return [_to_dict(a) for a in items]
