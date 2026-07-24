# app/routers/plan_models.py
"""
Compatibility shim.

Historically, some routers/services imported ORM models from app.routers.plan_models.
The authoritative ORM models now live in app.models (single source of truth) to avoid
duplicate table registration in SQLAlchemy metadata.

Keep these re-exports so existing imports keep working:
    from app.routers.plan_models import Plan, PlanMeal, PlanItem, EnergyTarget, UserPreference
"""

from __future__ import annotations

from app.models import EnergyTarget, Plan, PlanItem, PlanMeal, UserPreference

__all__ = [
    "Plan",
    "PlanMeal",
    "PlanItem",
    "EnergyTarget",
    "UserPreference",
]
