from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Activity, PlannedWorkout, User
from app.services.workout_reconciliation import reconcile_planned_workouts, reconciliation_activity_window

FUELING_POLICY_VERSION = "2026-09-14.1"


@dataclass(frozen=True)
class MacroTargets:
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


@dataclass(frozen=True)
class TrainingContext:
    source: str
    window_hours: int
    confidence: str
    activity_count: int
    exercise_kcal: float
    duration_min: float
    distance_km: float
    sports: tuple[str, ...]
    latest_activity_at: str | None
    planned_workout_count: int = 0
    planned_duration_min: float = 0.0
    planned_sports: tuple[str, ...] = ()
    planned_intensity: str | None = None
    next_workout_at: str | None = None


@dataclass(frozen=True)
class NutritionAdjustment:
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float


@dataclass(frozen=True)
class TrainingNutritionResult:
    baseline: MacroTargets
    training: TrainingContext
    adjustment: NutritionAdjustment
    final: MacroTargets
    rationale: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"policy_version": FUELING_POLICY_VERSION, **asdict(self)}

    def cache_fingerprint(self) -> dict[str, Any]:
        return {
            "policy_version": FUELING_POLICY_VERSION,
            "training": asdict(self.training),
            "adjustment": asdict(self.adjustment),
            "final": asdict(self.final),
        }


def athlete_profile_baseline(user: User, plan_date: date) -> MacroTargets:
    """Return a stable, exercise-exclusive daily baseline for an athlete.

    Structured workouts are applied separately below.  Keeping them out of the
    baseline prevents an AI replan from using yesterday's already-adjusted meal
    totals and adding the same training fuel again.
    """
    weight_kg = _safe_float(getattr(user, "weight_kg", None))
    height_cm = _safe_float(getattr(user, "height_cm", None))
    dob = getattr(user, "dob", None)
    sex = str(getattr(user, "sex", None) or "unspecified").strip().lower()

    if weight_kg <= 0 or height_cm <= 0 or not isinstance(dob, date):
        # A consistent conservative default is safer than recycling mutable
        # plan totals for an incomplete profile.
        kcal = 2400.0
        protein_g = 150.0
        fat_g = 75.0
        carbs_g = (kcal - protein_g * 4.0 - fat_g * 9.0) / 4.0
        return MacroTargets(kcal=kcal, protein_g=protein_g, carbs_g=round(carbs_g, 1), fat_g=fat_g)

    age = plan_date.year - dob.year - ((plan_date.month, plan_date.day) < (dob.month, dob.day))
    sex_constant = 5.0 if sex == "male" else -161.0 if sex == "female" else -78.0
    bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * max(13, age) + sex_constant

    # 1.4 covers ordinary daily living and unstructured movement. Recorded or
    # planned workouts are intentionally excluded and periodized separately.
    kcal = bmr * 1.4
    goal = str(getattr(user, "goal", None) or "maintain").strip().lower()
    if goal in {"lose", "cut", "fat_loss"}:
        kcal -= 300.0
    elif goal in {"gain", "bulk", "lean_gain"}:
        kcal += 250.0
    kcal = min(4000.0, max(1500.0, kcal))

    protein_g = 1.8 * weight_kg
    fat_g = max(0.8 * weight_kg, kcal * 0.22 / 9.0)
    carbs_g = max(3.0 * weight_kg, (kcal - protein_g * 4.0 - fat_g * 9.0) / 4.0)
    # Keep energy internally consistent if the minimum carbohydrate floor is
    # higher than the initial Mifflin-derived estimate.
    kcal = protein_g * 4.0 + carbs_g * 4.0 + fat_g * 9.0
    return MacroTargets(
        kcal=round(kcal, 1),
        protein_g=round(protein_g, 1),
        carbs_g=round(carbs_g, 1),
        fat_g=round(fat_g, 1),
    )


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _activity_duration_s(activity: Activity) -> float:
    for field in ("duration_s", "moving_time_s", "elapsed_time"):
        value = _safe_float(getattr(activity, field, None))
        if value > 0:
            return value
    return 0.0


def _activity_distance_m(activity: Activity) -> float:
    for field in ("distance_m", "distance"):
        value = _safe_float(getattr(activity, field, None))
        if value > 0:
            return value
    return 0.0


def _estimated_met(sport: str) -> float:
    normalized = (sport or "").strip().lower()
    if any(token in normalized for token in ("hyrox", "hiit", "crossfit", "functional fitness")):
        return 8.5
    if any(token in normalized for token in ("run", "ride", "cycling", "bike", "triathlon")):
        return 8.0
    if any(token in normalized for token in ("swim", "row", "ski erg", "skierg")):
        return 7.0
    if any(token in normalized for token in ("walk", "hike", "elliptical", "stair")):
        return 5.0
    if any(token in normalized for token in ("weight", "strength", "crossfit")):
        return 6.0
    return 5.5


def _estimate_activity_kcal(activity: Activity, weight_kg: float) -> tuple[float, str]:
    reported = _safe_float(getattr(activity, "kcal", None))
    if reported > 0:
        duration_h = _activity_duration_s(activity) / 3600.0
        # Device energy is useful but occasionally corrupt. Fourteen MET-hours
        # is a deliberately generous ceiling for sustained athletic work.
        if duration_h > 0 and weight_kg > 0:
            reported = min(reported, 14.0 * weight_kg * duration_h)
        return reported, "high"

    duration_h = _activity_duration_s(activity) / 3600.0
    if duration_h <= 0 or weight_kg <= 0:
        return 0.0, "none"

    sport = str(getattr(activity, "sport", None) or getattr(activity, "provider", None) or "workout")
    estimated = _estimated_met(sport) * weight_kg * duration_h
    has_distance = _activity_distance_m(activity) > 0
    return estimated, "medium" if has_distance else "low"


def _confidence_level(levels: Iterable[str]) -> str:
    values = set(levels)
    if "high" in values:
        return "high"
    if "medium" in values:
        return "medium"
    if "low" in values:
        return "low"
    return "none"


def _recovery_fraction(goal: str | None, confidence: str) -> float:
    normalized_goal = (goal or "maintain").strip().lower()
    if any(token in normalized_goal for token in ("lose", "cut", "weight_loss")):
        base = 0.45
    elif any(token in normalized_goal for token in ("gain", "bulk", "muscle")):
        base = 0.75
    else:
        base = 0.65

    confidence_factor = {"high": 1.0, "medium": 0.85, "low": 0.65, "none": 0.0}[confidence]
    return base * confidence_factor


def _carb_recovery_g_per_kg(duration_min: float, kcal_per_hour: float) -> float:
    if duration_min < 30:
        base = 0.0
    elif duration_min < 60:
        base = 0.5
    elif duration_min < 90:
        base = 1.0
    elif duration_min < 150:
        base = 1.5
    else:
        base = 2.0

    if kcal_per_hour >= 600:
        base *= 1.2
    return min(2.5, base)


def _planned_carb_g_per_kg(duration_min: float, intensity: str) -> float:
    """Daily carbohydrate target band for an upcoming session."""
    duration_band = 0 if duration_min < 45 else 1 if duration_min < 90 else 2 if duration_min < 150 else 3
    table = {
        "easy": (3.0, 3.5, 4.0, 5.0),
        "moderate": (3.5, 4.0, 5.0, 6.0),
        "hard": (4.0, 5.0, 6.0, 7.0),
        "race": (4.5, 5.5, 7.0, 8.0),
    }
    return table.get(intensity, table["moderate"])[duration_band]


def _protein_target_g_per_kg(
    *,
    baseline_g_per_kg: float,
    duration_min: float,
    intensity: str,
    sports: Iterable[str],
    completed: bool,
) -> float:
    """Periodize protein conservatively within athlete consensus ranges.

    Protein does not need to rise in proportion to every additional training
    calorie.  A modest increase is useful for demanding recovery, resistance
    and hybrid work, while carbohydrate remains the primary workload lever.
    """
    normalized_intensity = (intensity or "moderate").strip().lower()
    sport_text = " ".join(str(sport or "").strip().lower() for sport in sports)
    resistance_or_hybrid = any(
        token in sport_text
        for token in (
            "strength",
            "weight",
            "hyrox",
            "hiit",
            "crossfit",
            "functional fitness",
            "ski erg",
            "skierg",
            "row",
        )
    )
    target = max(1.4, baseline_g_per_kg)
    if normalized_intensity in {"hard", "race"} or duration_min >= 150 or (completed and duration_min >= 90):
        target = max(target, 1.9)
    if resistance_or_hybrid and duration_min >= 45:
        target = max(target, 1.9)
    if (resistance_or_hybrid and normalized_intensity in {"hard", "race"}) or duration_min >= 180:
        target = max(target, 2.0)
    return min(2.2, target)


def _targets_for_training_load(
    *,
    baseline: MacroTargets,
    weight_kg: float,
    carb_floor_g: float,
    protein_floor_g: float,
    energy_floor_kcal: float,
) -> tuple[MacroTargets, NutritionAdjustment]:
    """Apply training floors while keeping calories and macros consistent."""
    carbs_g = max(baseline.carbs_g, carb_floor_g)
    protein_g = max(baseline.protein_g, protein_floor_g)
    fat_g = baseline.fat_g
    macro_kcal = protein_g * 4.0 + carbs_g * 4.0 + fat_g * 9.0

    # If carbohydrate/protein floors do not cover the conservative training
    # energy allowance, use a carbohydrate-forward 70/30 energy split. This
    # prevents a real workout from disappearing merely because baseline carbs
    # were already above the minimum band.
    remaining_kcal = max(0.0, energy_floor_kcal - macro_kcal)
    carbohydrate_kcal = remaining_kcal * 0.70
    carb_ceiling_g = max(baseline.carbs_g, 8.0 * weight_kg)
    carbohydrate_kcal = min(carbohydrate_kcal, max(0.0, carb_ceiling_g - carbs_g) * 4.0)
    carbs_g += carbohydrate_kcal / 4.0
    fat_g += (remaining_kcal - carbohydrate_kcal) / 9.0
    kcal = protein_g * 4.0 + carbs_g * 4.0 + fat_g * 9.0
    final = MacroTargets(
        kcal=round(kcal, 1),
        protein_g=round(protein_g, 1),
        carbs_g=round(carbs_g, 1),
        fat_g=round(fat_g, 1),
    )
    adjustment = NutritionAdjustment(
        kcal=round(final.kcal - baseline.kcal, 1),
        protein_g=round(final.protein_g - baseline.protein_g, 1),
        carbs_g=round(final.carbs_g - baseline.carbs_g, 1),
        fat_g=round(final.fat_g - baseline.fat_g, 1),
    )
    return final, adjustment


def _planned_exercise_kcal(workouts: Iterable[PlannedWorkout], weight_kg: float) -> float:
    intensity_factor = {"easy": 0.75, "moderate": 1.0, "hard": 1.15, "race": 1.25}
    return sum(
        _estimated_met(str(workout.sport or "workout"))
        * weight_kg
        * max(0.0, float(workout.duration_min or 0))
        / 60.0
        * intensity_factor.get(str(workout.intensity or "moderate").strip().lower(), 1.0)
        for workout in workouts
    )


def _apply_planned_fueling(
    result: TrainingNutritionResult,
    user: User,
    workouts: list[PlannedWorkout],
) -> TrainingNutritionResult:
    if not workouts:
        return result
    weight_kg = _safe_float(getattr(user, "weight_kg", None))
    if weight_kg <= 0:
        return replace(
            result,
            training=replace(result.training, planned_workout_count=len(workouts)),
            rationale=result.rationale + ("Upcoming training found; add body weight to personalize fueling.",),
        )

    intensity_rank = {"easy": 0, "moderate": 1, "hard": 2, "race": 3}
    duration_min = sum(workout.duration_min for workout in workouts)
    intensity = str(max(workouts, key=lambda row: intensity_rank.get(row.intensity, 1)).intensity or "moderate").lower()
    starts = sorted(workout.start_time for workout in workouts if workout.start_time)
    planned_carb_floor = weight_kg * _planned_carb_g_per_kg(duration_min, intensity)
    baseline_protein_g_per_kg = result.baseline.protein_g / weight_kg
    planned_protein_floor = weight_kg * _protein_target_g_per_kg(
        baseline_g_per_kg=baseline_protein_g_per_kg,
        duration_min=duration_min,
        intensity=intensity,
        sports=(workout.sport for workout in workouts),
        completed=False,
    )
    estimated_kcal = _planned_exercise_kcal(workouts, weight_kg)
    planned_energy_floor = result.baseline.kcal + estimated_kcal * _recovery_fraction(
        getattr(user, "goal", None), "medium"
    )
    final, adjustment = _targets_for_training_load(
        baseline=result.baseline,
        weight_kg=weight_kg,
        carb_floor_g=max(result.final.carbs_g, planned_carb_floor),
        protein_floor_g=max(result.final.protein_g, planned_protein_floor),
        energy_floor_kcal=max(result.final.kcal, planned_energy_floor),
    )
    planned_added_carbs = max(0.0, final.carbs_g - result.final.carbs_g)
    planned_added_protein = max(0.0, final.protein_g - result.final.protein_g)
    context = replace(
        result.training,
        planned_workout_count=len(workouts),
        planned_duration_min=float(duration_min),
        planned_sports=tuple(sorted({workout.sport for workout in workouts})),
        planned_intensity=intensity,
        next_workout_at=starts[0].isoformat() if starts else None,
    )
    rationale = result.rationale + (
        f"Fueling {len(workouts)} upcoming {intensity} session(s), totaling {duration_min} minutes.",
        f"Added {planned_added_carbs:.0f} g carbohydrate for planned training demand.",
        f"Added {planned_added_protein:.0f} g protein for training recovery demand.",
    )
    return TrainingNutritionResult(
        baseline=result.baseline,
        training=context,
        adjustment=adjustment,
        final=final,
        rationale=rationale,
    )


def calculate_from_activities(
    *,
    baseline: MacroTargets,
    user: User,
    activities: Iterable[Activity],
    window_hours: int = 48,
) -> TrainingNutritionResult:
    activity_rows = list(activities)
    weight_kg = _safe_float(getattr(user, "weight_kg", None))
    confidence_levels: list[str] = []
    exercise_kcal = 0.0
    duration_min = 0.0
    distance_km = 0.0
    sports: set[str] = set()
    providers: set[str] = set()
    latest: datetime | None = None

    for activity in activity_rows:
        kcal, confidence = _estimate_activity_kcal(activity, weight_kg)
        exercise_kcal += kcal
        confidence_levels.append(confidence)
        duration_min += _activity_duration_s(activity) / 60.0
        distance_km += _activity_distance_m(activity) / 1000.0
        sports.add(str(getattr(activity, "sport", None) or "Workout"))
        provider = str(
            getattr(activity, "source_provider", None) or getattr(activity, "provider", None) or "activity"
        ).lower()
        providers.add(provider)
        started = getattr(activity, "start_time", None)
        if isinstance(started, datetime):
            comparable = started.replace(tzinfo=None) if started.tzinfo else started
            if latest is None or comparable > latest:
                latest = comparable

    confidence = _confidence_level(confidence_levels)
    if not activity_rows:
        source = "none"
    elif providers == {"strava"}:
        source = "strava"
    elif len(providers) > 1:
        source = "mixed"
    else:
        source = next(iter(providers), "activity")

    training = TrainingContext(
        source=source,
        window_hours=window_hours,
        confidence=confidence,
        activity_count=len(activity_rows),
        exercise_kcal=round(exercise_kcal, 1),
        duration_min=round(duration_min, 1),
        distance_km=round(distance_km, 2),
        sports=tuple(sorted(sports)),
        latest_activity_at=latest.isoformat() if latest else None,
    )

    if not activity_rows or weight_kg <= 0 or duration_min <= 0:
        zero = NutritionAdjustment(kcal=0.0, protein_g=0.0, carbs_g=0.0, fat_g=0.0)
        return TrainingNutritionResult(
            baseline=baseline,
            training=training,
            adjustment=zero,
            final=baseline,
            rationale=("No reliable recent activity adjustment; profile-based targets were used.",),
        )

    kcal_per_hour = exercise_kcal / max(duration_min / 60.0, 0.25)
    recovery_band = 3.0 + _carb_recovery_g_per_kg(duration_min, kcal_per_hour)
    if kcal_per_hour >= 600:
        recovery_band += 0.5
    recovery_band = min(8.0, recovery_band)
    baseline_protein_g_per_kg = baseline.protein_g / weight_kg
    protein_floor = weight_kg * _protein_target_g_per_kg(
        baseline_g_per_kg=baseline_protein_g_per_kg,
        duration_min=duration_min,
        intensity="hard" if kcal_per_hour >= 600 else "moderate",
        sports=sports,
        completed=True,
    )
    energy_floor = baseline.kcal + exercise_kcal * _recovery_fraction(getattr(user, "goal", None), confidence)
    final, adjustment = _targets_for_training_load(
        baseline=baseline,
        weight_kg=weight_kg,
        carb_floor_g=weight_kg * recovery_band,
        protein_floor_g=protein_floor,
        energy_floor_kcal=energy_floor,
    )
    rationale = (
        f"Used {len(activity_rows)} recent workout(s) from the prior {window_hours} hours.",
        f"Added {adjustment.carbs_g:.0f} g carbohydrate for estimated glycogen recovery.",
        f"Added {adjustment.protein_g:.0f} g protein for tissue repair and adaptation.",
        f"Periodized total energy by {adjustment.kcal:.0f} kcal with {confidence} confidence.",
    )
    return TrainingNutritionResult(
        baseline=baseline,
        training=training,
        adjustment=adjustment,
        final=final,
        rationale=rationale,
    )


def calculate_training_nutrition(
    *,
    db: Session,
    user: User,
    plan_date: date,
    baseline: MacroTargets,
    window_hours: int = 48,
    now: datetime | None = None,
) -> TrainingNutritionResult:
    now_utc = now or datetime.now(UTC)
    now_naive = now_utc.replace(tzinfo=None) if now_utc.tzinfo else now_utc
    plan_day_end = datetime.combine(plan_date, time.max)
    window_end = min(plan_day_end, now_naive)
    window_start = plan_day_end - timedelta(hours=window_hours)

    if window_start > window_end:
        activities: list[Activity] = []
    else:
        activities = (
            db.query(Activity)
            .filter(
                Activity.user_id == user.id,
                Activity.start_time >= window_start,
                Activity.start_time <= window_end,
            )
            .order_by(Activity.start_time.asc())
            .all()
        )
    recovery_result = calculate_from_activities(
        baseline=baseline,
        user=user,
        activities=activities,
        window_hours=window_hours,
    )
    planned_query = db.query(PlannedWorkout).filter(
        PlannedWorkout.user_id == user.id,
        PlannedWorkout.workout_date == plan_date,
    )
    if plan_date == now_naive.date():
        planned_query = planned_query.filter(
            (PlannedWorkout.start_time.is_(None)) | (PlannedWorkout.start_time >= now_naive)
        )
    planned = planned_query.order_by(PlannedWorkout.start_time, PlannedWorkout.id).all()
    match_start, match_end = reconciliation_activity_window(plan_date, plan_date)
    matching_activities = (
        db.query(Activity)
        .filter(
            Activity.user_id == user.id,
            Activity.start_time >= match_start,
            Activity.start_time < match_end,
            Activity.start_time <= now_naive,
        )
        .order_by(Activity.start_time.asc())
        .all()
    )
    planned, _ = reconcile_planned_workouts(planned, matching_activities, user, now=now_naive)
    return _apply_planned_fueling(recovery_result, user, planned)
