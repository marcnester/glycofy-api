from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Activity
from app.models_plan import ActivityDailySummary


def _intensity_from_kcal(kcal: int) -> str:
    if kcal >= 1500:
        return "race"
    if kcal >= 1000:
        return "hard"
    if kcal >= 400:
        return "moderate"
    return "easy"


def rebuild_daily_summaries(db: Session, user_id: int, start: date, end: date) -> int:
    """
    Recompute daily summaries between [start, end] inclusive from Activity rows.
    Returns number of days written.
    """
    # aggregate by UTC date of start_time
    q = (
        db.query(
            func.date(Activity.start_time).label("d"),
            func.coalesce(func.sum(Activity.kcal), 0).label("kcal"),
            func.coalesce(func.sum(Activity.duration_s), 0).label("dur_s"),
            Activity.sport.label("sport"),
        )
        .filter(Activity.user_id == user_id)
        .filter(func.date(Activity.start_time) >= start)
        .filter(func.date(Activity.start_time) <= end)
        .group_by("d", Activity.sport)
        .order_by("d")
    )

    per_day: dict[date, dict[str, Any]] = {}
    for row in q.all():
        d = row.d
        sport = row.sport or "Workout"
        if d not in per_day:
            per_day[d] = {"kcal": 0, "dur": 0, "by": {}}
        per_day[d]["kcal"] += int(row.kcal or 0)
        per_day[d]["dur"] += int((row.dur_s or 0) // 60)
        per_day[d]["by"][sport] = per_day[d]["by"].get(sport, 0) + int(row.kcal or 0)

    written = 0
    for d, agg in per_day.items():
        inst = (
            db.query(ActivityDailySummary)
            .filter(ActivityDailySummary.user_id == user_id, ActivityDailySummary.date == d)
            .first()
        )
        if not inst:
            inst = ActivityDailySummary(user_id=user_id, date=d)
            db.add(inst)
        inst.kcal_exercise = int(agg["kcal"])
        inst.duration_min = int(agg["dur"])
        inst.sport_breakdown = agg["by"]
        inst.intensity_hint = _intensity_from_kcal(inst.kcal_exercise)
        written += 1

    db.commit()
    return written


def window_rows(db: Session, user_id: int, end_date: date, window: int):
    start = end_date - timedelta(days=window - 1)
    rows = (
        db.query(ActivityDailySummary)
        .filter(ActivityDailySummary.user_id == user_id)
        .filter(ActivityDailySummary.date >= start)
        .filter(ActivityDailySummary.date <= end_date)
        .order_by(ActivityDailySummary.date.asc())
        .all()
    )
    return rows
