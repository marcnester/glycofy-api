from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Activity, PlannedWorkout, User
from app.services.training_nutrition import (
    MacroTargets,
    _apply_planned_fueling,
    athlete_profile_baseline,
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
    assert result.final.protein_g >= BASELINE.protein_g
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
    assert result.adjustment.carbs_g >= 160
    assert result.adjustment.kcal >= 640
    assert result.final.carbs_g >= 70 * 6


def test_profile_baseline_is_stable_and_excludes_structured_training():
    user = _user()
    user.sex = "male"
    user.dob = date(1980, 6, 1)
    user.height_cm = 180

    first = athlete_profile_baseline(user, date(2026, 9, 11))
    repeated = athlete_profile_baseline(user, date(2026, 9, 11))

    assert first == repeated
    assert 2000 <= first.kcal <= 3000
    assert first.protein_g == 126
    assert first.carbs_g >= 210
    assert first.kcal == round(first.protein_g * 4 + first.carbs_g * 4 + first.fat_g * 9, 1)


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


def _planned_result(*, duration: int, intensity: str, sport: str = "Cycling", weight_kg: float = 70):
    user = _user()
    user.weight_kg = weight_kg
    protein_g = 1.6 * weight_kg
    carbs_g = 3.0 * weight_kg
    fat_g = 0.8 * weight_kg
    baseline = MacroTargets(
        kcal=protein_g * 4 + carbs_g * 4 + fat_g * 9,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
    )
    empty = calculate_from_activities(baseline=baseline, user=user, activities=[])
    workout = PlannedWorkout(
        user_id=1,
        workout_date=date(2026, 9, 12),
        start_time=datetime(2026, 9, 12, 9),
        sport=sport,
        duration_min=duration,
        intensity=intensity,
        priority="normal",
        source="manual",
    )
    return _apply_planned_fueling(empty, user, [workout])


def test_exhaustive_planned_workload_matrix_is_monotonic_and_plausible():
    """Exercise 960 athlete/session combinations across key boundary values."""
    durations = (15, 30, 44, 45, 60, 89, 90, 120, 149, 150, 180, 240)
    intensities = ("easy", "moderate", "hard", "race")
    for weight_kg in (50, 70, 90, 120):
        for sport in ("Cycling", "Running", "Strength", "HYROX", "Rowing"):
            by_intensity = {
                intensity: [
                    _planned_result(duration=duration, intensity=intensity, sport=sport, weight_kg=weight_kg)
                    for duration in durations
                ]
                for intensity in intensities
            }
            for results in by_intensity.values():
                assert [row.final.kcal for row in results] == sorted(row.final.kcal for row in results)
                assert [row.final.carbs_g for row in results] == sorted(row.final.carbs_g for row in results)
                assert [row.final.protein_g for row in results] == sorted(row.final.protein_g for row in results)
                for row in results:
                    assert 1.4 <= row.final.protein_g / weight_kg <= 2.2
                    assert 3.0 <= row.final.carbs_g / weight_kg <= 8.0
                    macro_kcal = row.final.protein_g * 4 + row.final.carbs_g * 4 + row.final.fat_g * 9
                    assert abs(row.final.kcal - macro_kcal) < 1.0
            for index in range(len(durations)):
                workload_rows = [by_intensity[intensity][index] for intensity in intensities]
                assert [row.final.kcal for row in workload_rows] == sorted(row.final.kcal for row in workload_rows)
                assert [row.final.carbs_g for row in workload_rows] == sorted(
                    row.final.carbs_g for row in workload_rows
                )


def test_hard_hybrid_training_receives_modest_protein_periodization():
    easy = _planned_result(duration=60, intensity="easy", sport="Cycling")
    hard_hyrox = _planned_result(duration=60, intensity="hard", sport="HYROX")

    assert easy.final.protein_g == 70 * 1.6
    assert hard_hyrox.final.protein_g == 70 * 2.0
    assert hard_hyrox.final.carbs_g > easy.final.carbs_g
    assert hard_hyrox.final.kcal > easy.final.kcal


def test_completed_workload_duration_and_energy_scale_recovery_targets():
    durations = (30, 60, 90, 150, 240)
    results = []
    for duration in durations:
        workout = SimpleNamespace(
            kcal=duration * 10,
            duration_s=duration * 60,
            distance_m=duration * 200,
            sport="Run",
            source_provider="strava",
            start_time=datetime(2026, 9, 12, 8),
        )
        results.append(calculate_from_activities(baseline=BASELINE, user=_user(), activities=[workout]))

    assert [row.final.kcal for row in results] == sorted(row.final.kcal for row in results)
    assert [row.final.carbs_g for row in results] == sorted(row.final.carbs_g for row in results)
    assert [row.final.protein_g for row in results] == sorted(row.final.protein_g for row in results)
    assert results[-1].final.kcal > results[0].final.kcal


def test_implausible_device_calories_are_bounded_before_fueling():
    workout = SimpleNamespace(
        kcal=10000,
        duration_s=3600,
        distance_m=10000,
        sport="Run",
        source_provider="strava",
        start_time=datetime(2026, 9, 12, 8),
    )
    result = calculate_from_activities(baseline=BASELINE, user=_user(), activities=[workout])

    assert result.training.exercise_kcal == 14 * 70
    assert result.final.carbs_g <= 8 * 70
