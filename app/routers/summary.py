# app/routers/summary.py
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Activity, User
from app.routers.auth import get_current_user

router = APIRouter()


def _coerce_date(v) -> date:
    """
    Try to coerce a value from the DB/model into a date.
    Supports:
      - datetime => .date()
      - date     => itself
      - str(YYYY-MM-DD) => parsed
    """
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def _activity_date(act: Activity) -> date | None:
    """
    Extract a date for grouping from the Activity instance.
    We try the common fields in order to be resilient to schema differences.
    """
    for attr in ("date", "start_date", "start_time", "start_at", "started_at"):
        if hasattr(act, attr):
            return _coerce_date(getattr(act, attr))
    return None


def _daterange_inclusive(d0: date, d1: date) -> list[date]:
    step = 1 + (d1 - d0).days
    return [d0 + timedelta(days=i) for i in range(max(step, 0))]


@router.get("/summary", summary="Weekly summary over a date range")
def get_summary(
    from_: str | None = Query(None, alias="from", description="YYYY-MM-DD (inclusive)"),
    to: str | None = Query(None, description="YYYY-MM-DD (inclusive)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns a summary object like:
    {
      "from": "YYYY-MM-DD",
      "to":   "YYYY-MM-DD",
      "diet_pref": "...",
      "total_training_kcal": 0,
      "total_planned_kcal":  0,
      "total_activities":    0,
      "days": [
        {
          "date": "YYYY-MM-DD",
          "training_kcal": 0,
          "planned_kcal":  0,
          "activities": [ {"sport":"cycling","kcal":1291}, {"sport":"strength","kcal":0} ],
          "meals": [],
          "diet_pref": "omnivore"
        },
        ...
      ]
    }
    Notes:
      * Defaults to the last 7 days when from/to are omitted.
      * Includes zero-kcal activities (so 'strength: 0 kcal' appears instead of a blank).
      * planned_kcal mirrors training_kcal for now (placeholder for future logic).
    """

    # ----- Resolve date range (default last 7 days, inclusive) -----
    today = date.today()
    if to:
        try:
            to_d = datetime.strptime(to[:10], "%Y-%m-%d").date()
        except Exception:
            to_d = today
    else:
        to_d = today

    if from_:
        try:
            from_d = datetime.strptime(from_[:10], "%Y-%m-%d").date()
        except Exception:
            from_d = to_d - timedelta(days=6)
    else:
        from_d = to_d - timedelta(days=6)

    if from_d > to_d:
        # swap if needed
        from_d, to_d = to_d, from_d

    # ----- Query activities for the user within the window -----
    # We don't know your exact Activity schema; this is robust to different
    # "date-ish" columns because we post-filter by computed _activity_date().
    q = db.query(Activity).filter(Activity.user_id == user.id)
    activities: list[Activity] = q.all()

    # Filter to range using extracted date
    acts_in_range: list[Activity] = []
    for a in activities:
        ad = _activity_date(a)
        if ad is None:
            continue
        if from_d <= ad <= to_d:
            acts_in_range.append(a)

    # ----- Prepare day buckets -----
    days_map: dict[str, dict] = {}
    for d in _daterange_inclusive(from_d, to_d):
        key = d.isoformat()
        days_map[key] = {
            "date": key,
            "training_kcal": 0,
            "planned_kcal": 0,  # we’ll set = training_kcal later
            "activities": [],  # filled below from _sports
            "meals": [],  # placeholder for future plan integration
            "diet_pref": user.diet_pref or "omnivore",
            "_sports": {},  # temp map: sport -> kcal
            "_count": 0,  # number of activities (all, including zero-kcal)
        }

    # ----- Aggregate activities -----
    total_activities = 0
    for a in acts_in_range:
        ad = _activity_date(a)
        if ad is None:
            continue
        k = ad.isoformat()
        if k not in days_map:
            continue

        sport = (getattr(a, "sport", None) or "unknown").strip().lower()
        kcal = getattr(a, "kcal", 0)
        try:
            kcal = int(kcal or 0)
        except Exception:
            kcal = 0
        kcal = max(kcal, 0)  # never negative

        # Count all activities (even when kcal == 0)
        days_map[k]["_count"] += 1
        total_activities += 1

        # Sum training kcal
        days_map[k]["training_kcal"] += kcal

        # Accumulate sport totals (include zero-kcal so the sport shows up)
        days_map[k]["_sports"][sport] = days_map[k]["_sports"].get(sport, 0) + kcal

    # ----- Finalize per-day arrays & totals -----
    total_training_kcal = 0
    for d in _daterange_inclusive(from_d, to_d):
        key = d.isoformat()
        day = days_map[key]

        # Build visible activities list with sport breakdown (including zeros),
        # sorted by kcal desc to keep nicest order.
        sports_items = sorted(day["_sports"].items(), key=lambda x: x[1], reverse=True)
        day["activities"] = [{"sport": s, "kcal": int(k)} for s, k in sports_items]

        # planned_kcal currently mirrors training_kcal (placeholder for future logic)
        day["planned_kcal"] = int(day["training_kcal"])

        total_training_kcal += int(day["training_kcal"])

        # Clean temp fields
        day.pop("_sports", None)
        day.pop("_count", None)

    # For now mirror totals as in your existing payloads
    total_planned_kcal = total_training_kcal

    # ----- Build response -----
    resp = {
        "from": from_d.isoformat(),
        "to": to_d.isoformat(),
        "diet_pref": user.diet_pref or "omnivore",
        "total_training_kcal": int(total_training_kcal),
        "total_planned_kcal": int(total_planned_kcal),
        "total_activities": int(total_activities),
        "days": [days_map[d.isoformat()] for d in _daterange_inclusive(from_d, to_d)],
    }
    return resp
