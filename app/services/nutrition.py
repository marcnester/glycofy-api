# app/services/nutrition.py
"""
Nutrition target computations for Glycofy.

- BMR: Mifflin–St Jeor
- Base TDEE = BMR * activity_factor (default 2.0 ~ "very active")
- training_kcal is *reported separately* and NOT included when filling macros.
- Macros:
    protein_g = round(1.8 * weight_kg)
    carbs_g   = round(6.0 * weight_kg)
    fat_g     = remainder of (base TDEE - protein_kcal - carb_kcal) / 9

`goal` can slightly adjust base TDEE before macros:
    - "cut"       => -300 kcal
    - "recomp"    => -100 kcal
    - "maintain"  => 0 kcal
    - "lean_gain" => +200 kcal
    - "bulk"      => +300 kcal

The function signature matches what the plans router expects.
"""

from __future__ import annotations


def _bmr_msj(sex: str, age_years: int, height_cm: float, weight_kg: float) -> float:
    sex = (sex or "male").lower()
    if sex not in ("male", "female"):
        sex = "male"
    # Mifflin–St Jeor
    if sex == "male":
        return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years + 5.0
    else:
        return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years - 161.0


def _goal_adjust_kcal(goal: str | None) -> int:
    g = (goal or "maintain").lower()
    if g in ("cut", "fat_loss", "lose"):
        return -300
    if g in ("recomp",):
        return -100
    if g in ("lean_gain", "slow_gain"):
        return +200
    if g in ("bulk", "gain"):
        return +300
    return 0  # maintain


def compute_targets(
    *,
    sex: str,
    age_years: int,
    height_cm: float,
    weight_kg: float,
    goal: str = "maintain",
    training_kcal: int = 0,
    activity_factor: float = 2.0,  # matches prior outputs (~very active)
) -> dict[str, int]:
    """
    Return daily targets as integers:
        tdee_kcal (base, after goal adjust), protein_g, carbs_g, fat_g, training_kcal
    """
    # 1) Base energy
    bmr = _bmr_msj(sex, age_years, height_cm, weight_kg)
    base_tdee = bmr * float(activity_factor)
    base_tdee += _goal_adjust_kcal(goal)
    # Ensure not negative
    base_tdee = max(base_tdee, 1200)

    # 2) Macros (on base TDEE only)
    protein_g = round(1.8 * weight_kg)  # ~ endurance + lifting blend
    carbs_g = round(6.0 * weight_kg)  # endurance-focused
    protein_kcal = protein_g * 4
    carbs_kcal = carbs_g * 4

    fat_kcal = max(0, int(round(base_tdee)) - protein_kcal - carbs_kcal)
    fat_g = max(0, round(fat_kcal / 9))

    return {
        "tdee_kcal": int(round(base_tdee)),  # base (excludes training_kcal)
        "protein_g": int(protein_g),
        "carbs_g": int(carbs_g),
        "fat_g": int(fat_g),
        "training_kcal": int(training_kcal),
    }
