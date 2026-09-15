from datetime import UTC, date, datetime

from app.models import Activity, PlannedWorkout, User
from app.services.workout_reconciliation import reconcile_planned_workouts, sport_family


def _user(timezone: str = "UTC") -> User:
    return User(id=1, email="athlete@example.com", password_hash="test", timezone=timezone)


def _plan(*, workout_id: int, sport: str, duration: int, day: date, hour: int | None = None) -> PlannedWorkout:
    return PlannedWorkout(
        id=workout_id,
        user_id=1,
        workout_date=day,
        start_time=datetime.combine(day, datetime.min.time()).replace(hour=hour) if hour is not None else None,
        sport=sport,
        duration_min=duration,
        intensity="moderate",
        priority="normal",
        source="manual",
    )


def _activity(*, activity_id: int, sport: str, duration: int, started: datetime) -> Activity:
    return Activity(
        id=activity_id,
        user_id=1,
        provider="strava",
        source_provider="strava",
        source_id=str(activity_id),
        start_time=started,
        duration_s=duration * 60,
        sport=sport,
    )


def test_sport_aliases_share_training_families():
    assert sport_family("WeightTraining") == sport_family("Strength")
    assert sport_family("VirtualRide") == sport_family("Cycling")
    assert sport_family("High Intensity Interval Training") == sport_family("HIIT")


def test_matching_strava_actual_supersedes_manual_plan():
    day = date(2026, 9, 14)
    planned = [_plan(workout_id=1, sport="Strength", duration=75, day=day, hour=16)]
    activities = [
        _activity(
            activity_id=10,
            sport="WeightTraining",
            duration=89,
            started=datetime(2026, 9, 14, 16),
        )
    ]

    unmatched, matches = reconcile_planned_workouts(
        planned,
        activities,
        _user(),
        now=datetime(2026, 9, 14, 20, tzinfo=UTC),
    )

    assert unmatched == []
    assert matches == {1: activities[0]}


def test_different_sport_does_not_supersede_plan():
    day = date(2026, 9, 14)
    planned = [_plan(workout_id=1, sport="Strength", duration=75, day=day)]
    activities = [_activity(activity_id=10, sport="Ride", duration=75, started=datetime(2026, 9, 14, 16))]

    unmatched, matches = reconcile_planned_workouts(
        planned,
        activities,
        _user(),
        now=datetime(2026, 9, 14, 20, tzinfo=UTC),
    )

    assert unmatched == planned
    assert matches == {}


def test_one_actual_only_supersedes_one_of_multiple_same_day_sessions():
    day = date(2026, 9, 14)
    planned = [
        _plan(workout_id=1, sport="Strength", duration=45, day=day, hour=8),
        _plan(workout_id=2, sport="Strength", duration=90, day=day, hour=16),
    ]
    activity = _activity(activity_id=10, sport="WeightTraining", duration=89, started=datetime(2026, 9, 14, 16))

    unmatched, matches = reconcile_planned_workouts(
        planned,
        [activity],
        _user(),
        now=datetime(2026, 9, 14, 20, tzinfo=UTC),
    )

    assert [workout.id for workout in unmatched] == [1]
    assert matches == {2: activity}


def test_user_timezone_matches_activity_across_utc_date_rollover():
    local_day = date(2026, 9, 14)
    planned = [_plan(workout_id=1, sport="Run", duration=60, day=local_day, hour=None)]
    activity = _activity(
        activity_id=10,
        sport="Run",
        duration=63,
        started=datetime(2026, 9, 15, 3),
    )

    unmatched, matches = reconcile_planned_workouts(
        planned,
        [activity],
        _user("America/Los_Angeles"),
        now=datetime(2026, 9, 15, 4, tzinfo=UTC),
    )

    assert unmatched == []
    assert matches == {1: activity}
