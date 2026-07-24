# app/services/energy.py
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

# We assume you already have an Activity model (from Strava sync) with fields like:
#   user_id:int, start_time:datetime, sport:str, calories_kcal:float (or calories:float)
#   Optional fields we try to read if calories are missing: distance_m, moving_time_s
from app.models import Activity  # adjust import if your Activity lives elsewhere

# -----------------------------
# Small helpers
# -----------------------------


def _as_date(dt: datetime) -> date:
    if isinstance(dt, datetime):
        return dt.date()
    return dt  # type: ignore[return-value]


def _get_calories(act: Activity) -> float:
    """Prefer a calories field; otherwise return 0.0 (we avoid guessing)."""
    for attr in ("calories_kcal", "calories", "cal"):
        if hasattr(act, attr) and getattr(act, attr) is not None:
            try:
                return float(getattr(act, attr) or 0.0)
            except Exception:
                pass
    return 0.0


def _sum_by_day(acts: Iterable[Activity]) -> dict[date, float]:
    out: dict[date, float] = defaultdict(float)
    for a in acts:
        when = getattr(a, "start_time", None) or getattr(a, "start_date", None)
        if when is None:
            # Fall back to created_at if start is missing
            when = getattr(a, "created_at", None)
        if when is None:
            continue
        d = _as_date(when)
        out[d] += _get_calories(a)
    return dict(out)


# -----------------------------
# Core calculation
# -----------------------------


def estimate_tdee_kcal(
    *,
    weight_kg: float | None = None,
    baseline_kcal: float | None = None,
) -> float:
    """
    A conservative default. If weight is known, ~30 kcal/kg/day is a decent athletic baseline.
    Otherwise fall back to 2200 kcal.
    """
    if baseline_kcal and baseline_kcal > 0:
        return float(baseline_kcal)
    if weight_kg and weight_kg > 0:
        return float(round(30.0 * weight_kg))
    return 2200.0


def compute_daily_target(
    *,
    tdee_kcal: float,
    day_training_kcal: float,
    recovery_multiplier: float = 1.10,
) -> float:
    """
    Simple model: TDEE + (training kcal * recovery multiplier).
    Recovery multiplier (>1) covers glycogen restoration & adaptation.
    """
    return float(round(tdee_kcal + day_training_kcal * recovery_multiplier))


def compute_energy_targets_for_window(
    db: Session,
    *,
    user_id: int,
    end_date: date,
    days: int = 7,
    weight_kg: float | None = None,
    baseline_kcal: float | None = None,
    recovery_multiplier: float = 1.10,
) -> dict[date, tuple[float, float, float]]:
    """
    Returns a dict keyed by date -> (tdee_kcal, training_kcal, target_kcal)
    for each day in [end_date - days + 1, end_date].

    We **only** sum explicit activity calories; we do not guess them.
    """
    start_date = end_date - timedelta(days=days - 1)

    # Pull activities in window
    q = (
        db.query(Activity)
        .filter(Activity.user_id == user_id)
        .filter(Activity.start_time >= datetime.combine(start_date, datetime.min.time()))
        .filter(Activity.start_time < datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
    )
    acts = list(q)

    # Sum kcal per day
    kcal_by_day = _sum_by_day(acts)

    results: dict[date, tuple[float, float, float]] = {}
    tdee = estimate_tdee_kcal(weight_kg=weight_kg, baseline_kcal=baseline_kcal)

    for i in range(days):
        d = start_date + timedelta(days=i)
        training_kcal = float(round(kcal_by_day.get(d, 0.0)))
        target = compute_daily_target(
            tdee_kcal=tdee,
            day_training_kcal=training_kcal,
            recovery_multiplier=recovery_multiplier,
        )
        results[d] = (tdee, training_kcal, target)

    return results
