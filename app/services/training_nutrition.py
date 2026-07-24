from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Activity, User


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
        return asdict(self)

    def cache_fingerprint(self) -> dict[str, Any]:
        return {
            "training": asdict(self.training),
            "adjustment": asdict(self.adjustment),
            "final": asdict(self.final),
        }


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
    if any(token in normalized for token in ("run", "ride", "cycling", "bike", "triathlon")):
        return 8.0
    if "swim" in normalized:
        return 7.0
    if any(token in normalized for token in ("walk", "hike")):
        return 5.0
    if any(token in normalized for token in ("weight", "strength", "crossfit")):
        return 6.0
    return 5.5


def _estimate_activity_kcal(activity: Activity, weight_kg: float) -> tuple[float, str]:
    reported = _safe_float(getattr(activity, "kcal", None))
    if reported > 0:
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
    carbs_g = weight_kg * _carb_recovery_g_per_kg(duration_min, kcal_per_hour)
    protein_g = min(15.0, weight_kg * 0.1) if duration_min >= 60 else 0.0
    macro_kcal = carbs_g * 4.0 + protein_g * 4.0
    recovery_kcal = min(1200.0, exercise_kcal * _recovery_fraction(getattr(user, "goal", None), confidence))
    adjustment_kcal = max(macro_kcal, recovery_kcal)
    fat_g = min(10.0, max(0.0, adjustment_kcal - macro_kcal) / 9.0)
    adjustment_kcal = carbs_g * 4.0 + protein_g * 4.0 + fat_g * 9.0

    adjustment = NutritionAdjustment(
        kcal=round(adjustment_kcal, 1),
        protein_g=round(protein_g, 1),
        carbs_g=round(carbs_g, 1),
        fat_g=round(fat_g, 1),
    )
    final = MacroTargets(
        kcal=round(baseline.kcal + adjustment.kcal, 1),
        protein_g=round(baseline.protein_g + adjustment.protein_g, 1),
        carbs_g=round(baseline.carbs_g + adjustment.carbs_g, 1),
        fat_g=round(baseline.fat_g + adjustment.fat_g, 1),
    )
    rationale = (
        f"Used {len(activity_rows)} recent workout(s) from the prior {window_hours} hours.",
        f"Added {adjustment.carbs_g:.0f} g carbohydrate for estimated glycogen recovery.",
        f"Added {adjustment.protein_g:.0f} g protein and {adjustment.kcal:.0f} kcal with {confidence} confidence.",
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
    return calculate_from_activities(
        baseline=baseline,
        user=user,
        activities=activities,
        window_hours=window_hours,
    )
