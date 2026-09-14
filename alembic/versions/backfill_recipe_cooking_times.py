"""backfill safe reusable recipe cooking times

Revision ID: backfill_recipe_cooking_times
Revises: add_recipe_cooking_times
"""

import json
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "backfill_recipe_cooking_times"
down_revision = "add_recipe_cooking_times"
branch_labels = None
depends_on = None


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _safe_timing(meta: dict[str, Any]) -> tuple[int, int, int] | None:
    try:
        prep = int(meta["prep_time_min"])
        cook = int(meta["cook_time_min"])
        total = int(meta["total_time_min"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (1 <= prep <= 120 and 0 <= cook <= 180 and 1 <= total <= 240):
        return None
    if total < prep + cook or total > prep + cook + 60:
        return None
    return prep, cook, total


def upgrade() -> None:
    bind = op.get_bind()
    recipes = sa.table(
        "recipes",
        sa.column("id", sa.Integer),
        sa.column("title", sa.String),
        sa.column("instructions", sa.String),
        sa.column("prep_time_min", sa.Integer),
        sa.column("cook_time_min", sa.Integer),
        sa.column("total_time_min", sa.Integer),
    )
    plan_meals = sa.table(
        "plan_meals",
        sa.column("id", sa.Integer),
        sa.column("recipe_id", sa.Integer),
        sa.column("title", sa.String),
        sa.column("instructions", sa.String),
        sa.column("meta", sa.JSON),
    )
    rows = bind.execute(
        sa.select(
            plan_meals.c.recipe_id,
            plan_meals.c.title,
            plan_meals.c.instructions,
            plan_meals.c.meta,
        )
        .where(plan_meals.c.recipe_id.is_not(None))
        .order_by(plan_meals.c.id)
    ).all()
    seen: set[int] = set()
    for raw_recipe_id, meal_title, meal_instructions, meta in rows:
        recipe_id = int(raw_recipe_id)
        if recipe_id in seen:
            continue
        timing = _safe_timing(_metadata(meta))
        if timing is None:
            continue
        recipe = bind.execute(
            sa.select(recipes.c.title, recipes.c.instructions).where(recipes.c.id == recipe_id)
        ).first()
        if recipe is None:
            continue
        recipe_title, recipe_instructions = recipe
        if str(recipe_title or "").strip() != str(meal_title or "").strip():
            continue
        if str(recipe_instructions or "").strip() != str(meal_instructions or "").strip():
            continue
        prep, cook, total = timing
        bind.execute(
            recipes.update()
            .where(recipes.c.id == recipe_id)
            .where(recipes.c.prep_time_min.is_(None))
            .values(prep_time_min=prep, cook_time_min=cook, total_time_min=total)
        )
        seen.add(recipe_id)


def downgrade() -> None:
    # The columns belong to the preceding schema migration. Clearing inferred
    # legacy values would also erase newly generated authoritative timings.
    pass
