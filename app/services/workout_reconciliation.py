from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Activity, PlannedWorkout, User

_SPORT_FAMILIES = {
    "bike": "cycling",
    "cycling": "cycling",
    "ebikeride": "cycling",
    "gravelride": "cycling",
    "mountainbikeride": "cycling",
    "ride": "cycling",
    "virtualcycling": "cycling",
    "virtualride": "cycling",
    "run": "running",
    "running": "running",
    "trailrun": "running",
    "virtualrun": "running",
    "weighttraining": "strength",
    "strength": "strength",
    "strengthtraining": "strength",
    "workout": "strength",
    "highintensityintervaltraining": "hiit",
    "hiit": "hiit",
    "crossfit": "hiit",
    "hyrox": "hiit",
    "row": "rowing",
    "rowing": "rowing",
    "virtualrow": "rowing",
    "skierg": "ski_erg",
    "nordicski": "ski_erg",
    "swim": "swimming",
    "swimming": "swimming",
}


def sport_family(value: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return _SPORT_FAMILIES.get(normalized, normalized)


def _user_timezone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(str(user.timezone or "UTC"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def activity_local_date(activity: Activity, user: User) -> date:
    started = activity.start_time
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started.astimezone(_user_timezone(user)).date()


def _duration_minutes(activity: Activity) -> float:
    return max(0.0, float(activity.duration_s or 0) / 60.0)


def _candidate_score(
    planned: PlannedWorkout,
    activity: Activity,
    user: User,
) -> float | None:
    if str(activity.source_provider or activity.provider or "").lower() != "strava":
        return None
    if sport_family(planned.sport) != sport_family(activity.sport):
        return None

    actual_duration = _duration_minutes(activity)
    planned_duration = max(0.0, float(planned.duration_min or 0))
    if actual_duration <= 0 or planned_duration <= 0:
        return None
    duration_difference = abs(actual_duration - planned_duration)
    if duration_difference > max(20.0, planned_duration * 0.40):
        return None

    day_difference = abs((activity_local_date(activity, user) - planned.workout_date).days)
    if day_difference > 1:
        return None

    time_difference_hours = 0.0
    if planned.start_time is not None:
        planned_start = planned.start_time
        actual_start = activity.start_time
        if planned_start.tzinfo is not None:
            planned_start = planned_start.astimezone(UTC).replace(tzinfo=None)
        if actual_start.tzinfo is not None:
            actual_start = actual_start.astimezone(UTC).replace(tzinfo=None)
        time_difference_hours = abs((actual_start - planned_start).total_seconds()) / 3600.0
        # Adjacent calendar dates are only accepted for the common local/UTC
        # rollover case, not as a generic next-day match.
        if day_difference and time_difference_hours > 14:
            return None

    return day_difference * 1000.0 + duration_difference * 10.0 + min(time_difference_hours, 24.0)


def reconcile_planned_workouts(
    planned: list[PlannedWorkout],
    activities: list[Activity],
    user: User,
    *,
    now: datetime | None = None,
) -> tuple[list[PlannedWorkout], dict[int, Activity]]:
    """Match completed Strava activities to planned sessions one-to-one.

    Planned rows remain stored for audit/history, while callers use the returned
    unmatched list for upcoming displays and nutrition calculations.
    """
    now_value = now or datetime.now(UTC)
    now_naive = now_value.astimezone(UTC).replace(tzinfo=None) if now_value.tzinfo else now_value
    completed = [activity for activity in activities if activity.start_time <= now_naive]
    candidates: list[tuple[float, int, int, PlannedWorkout, Activity]] = []
    for workout in planned:
        for activity in completed:
            score = _candidate_score(workout, activity, user)
            if score is not None:
                candidates.append((score, workout.id or 0, activity.id or 0, workout, activity))

    matched_plans: set[int] = set()
    matched_activities: set[int] = set()
    matches: dict[int, Activity] = {}
    for _, _, _, workout, activity in sorted(candidates, key=lambda row: row[:3]):
        workout_key = workout.id if workout.id is not None else id(workout)
        activity_key = activity.id if activity.id is not None else id(activity)
        if workout_key in matched_plans or activity_key in matched_activities:
            continue
        matched_plans.add(workout_key)
        matched_activities.add(activity_key)
        matches[workout_key] = activity

    unmatched = [
        workout for workout in planned if (workout.id if workout.id is not None else id(workout)) not in matched_plans
    ]
    return unmatched, matches


def reconciliation_activity_window(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(date_from - timedelta(days=1), datetime.min.time()),
        datetime.combine(date_to + timedelta(days=2), datetime.min.time()),
    )
