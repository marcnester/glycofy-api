from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Activity, PlannedWorkout, User
from app.services.training_nutrition import (
    MacroTargets,
    calculate_from_activities,
    calculate_training_nutrition,
)

BASELINE = MacroTargets(kcal=2200, protein_g=145, carbs_g=260, fat_g=65)


def _user() -> User:
    return User(
        id=1,
        email="athlete@example.com",
        password_hash="test",
        weight_kg=70,
        goal="maintain",
    )


def test_no_activity_uses_profile_baseline_without_adjustment():
    result = calculate_from_activities(
        baseline=BASELINE,
        user=_user(),
        activities=[],
    )

    assert result.final == BASELINE
    assert result.adjustment.kcal == 0
    assert result.training.source == "none"
    assert result.training.confidence == "none"


def test_recent_strava_workout_adds_bounded_recovery_targets():
    workout = SimpleNamespace(
        kcal=700,
        duration_s=5400,
        distance_m=30000,
        sport="Ride",
        source_provider="strava",
        start_time=datetime(2026, 7, 24, 8),
    )

    result = calculate_from_activities(
        baseline=BASELINE,
        user=_user(),
        activities=[workout],
    )

    assert result.training.source == "strava"
    assert result.training.confidence == "high"
    assert result.training.duration_min == 90
    assert result.adjustment.carbs_g > 0
    assert 0 < result.adjustment.protein_g <= 15
    assert 0 < result.adjustment.kcal <= 1200
    assert result.final.kcal > BASELINE.kcal


def test_duration_s_and_distance_support_estimate_when_strava_has_no_calories():
    workout = SimpleNamespace(
        kcal=None,
        duration_s=3600,
        distance_m=10000,
        sport="Run",
        source_provider="strava",
        start_time=datetime(2026, 7, 24, 8),
    )

    result = calculate_from_activities(
        baseline=BASELINE,
        user=_user(),
        activities=[workout],
    )

    assert result.training.duration_min == 60
    assert result.training.exercise_kcal > 0
    assert result.training.confidence == "medium"
    assert result.adjustment.carbs_g > 0


def test_hyrox_sports_receive_vigorous_fallback_energy_estimates():
    workout = SimpleNamespace(
        kcal=None,
        duration_s=3600,
        distance_m=0,
        sport="HYROX",
        source_provider="strava",
        start_time=datetime(2026, 7, 24, 8),
    )

    result = calculate_from_activities(baseline=BASELINE, user=_user(), activities=[workout])

    assert result.training.exercise_kcal == 595
    assert result.training.confidence == "low"


def test_database_window_ignores_activity_older_than_48_hours():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    with Session(engine) as db:
        user = _user()
        db.add(user)
        db.flush()
        db.add(
            Activity(
                user_id=1,
                start_time=(now - timedelta(days=4)).replace(tzinfo=None),
                duration_s=7200,
                distance_m=50000,
                kcal=1000,
                sport="Ride",
                source_provider="strava",
            )
        )
        db.commit()

        result = calculate_training_nutrition(
            db=db,
            user=user,
            plan_date=date(2026, 7, 24),
            baseline=BASELINE,
            now=now,
        )

    assert result.training.activity_count == 0
    assert result.final == BASELINE


def test_recent_activity_is_not_applied_to_a_plan_many_days_in_the_future():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    with Session(engine) as db:
        user = _user()
        db.add(user)
        db.flush()
        db.add(
            Activity(
                user_id=1,
                start_time=(now - timedelta(hours=2)).replace(tzinfo=None),
                duration_s=5400,
                distance_m=30000,
                kcal=700,
                sport="Ride",
                source_provider="strava",
            )
        )
        db.commit()

        result = calculate_training_nutrition(
            db=db,
            user=user,
            plan_date=date(2026, 7, 29),
            baseline=BASELINE,
            now=now,
        )

    assert result.training.activity_count == 0
    assert result.final == BASELINE


def test_future_hard_workout_adds_planned_fueling_without_completed_activity():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    with Session(engine) as db:
        user = _user()
        db.add(user)
        db.flush()
        db.add(
            PlannedWorkout(
                user_id=1,
                workout_date=date(2026, 7, 26),
                start_time=datetime(2026, 7, 26, 15),
                sport="Ride",
                duration_min=120,
                intensity="hard",
                priority="key",
                source="manual",
            )
        )
        db.commit()

        result = calculate_training_nutrition(
            db=db,
            user=user,
            plan_date=date(2026, 7, 26),
            baseline=BASELINE,
            now=now,
        )

    assert result.training.activity_count == 0
    assert result.training.planned_workout_count == 1
    assert result.training.planned_duration_min == 120
    assert result.training.planned_intensity == "hard"
    assert result.adjustment.carbs_g == 105
    assert result.adjustment.kcal == 420
    assert result.final.carbs_g == BASELINE.carbs_g + 105


def test_completed_time_on_today_planned_workout_is_not_double_counted():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)

    with Session(engine) as db:
        user = _user()
        db.add(user)
        db.flush()
        db.add(
            PlannedWorkout(
                user_id=1,
                workout_date=now.date(),
                start_time=datetime(2026, 7, 24, 8),
                sport="Run",
                duration_min=60,
                intensity="hard",
                priority="normal",
                source="manual",
            )
        )
        db.commit()

        result = calculate_training_nutrition(
            db=db,
            user=user,
            plan_date=now.date(),
            baseline=BASELINE,
            now=now,
        )

    assert result.training.planned_workout_count == 0
    assert result.final == BASELINE
