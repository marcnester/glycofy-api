"""create plan tables

Revision ID: 20251106_01
Revises: 31e2b9ffc120
Create Date: 2025-11-06

Notes:
- SQLite does not support ARRAY, so all list-ish fields use JSON with [] default.
- This migration is cross-dialect (SQLite locally, PostgreSQL in prod).
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from alembic import op

# Revision identifiers, used by Alembic.
revision = "20251106_01"
down_revision = "31e2b9ffc120"
branch_labels = None
depends_on = None


# Convenience server_default helpers for JSON on SQLite/Postgres
JSON_EMPTY_ARRAY = sa.text("'[]'")
JSON_EMPTY_OBJECT = sa.text("'{}'")


def upgrade() -> None:
    # ---- user_preferences ---------------------------------------------------
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        # Lists → JSON [] default (instead of ARRAY)
        sa.Column("dietary_tags", sa.JSON(), nullable=False, server_default=JSON_EMPTY_ARRAY),
        sa.Column("banned_ingredients", sa.JSON(), nullable=False, server_default=JSON_EMPTY_ARRAY),
        sa.Column("allergies", sa.JSON(), nullable=False, server_default=JSON_EMPTY_ARRAY),
        # Flexible per-user settings container (units, cuisine prefs, etc.)
        sa.Column("settings", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user_id"),
    )

    # ---- energy_targets (per-day macro/energy targets) ----------------------
    op.create_table(
        "energy_targets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("tdee_kcal", sa.Float, nullable=True),
        sa.Column("training_kcal", sa.Float, nullable=True),
        sa.Column("target_kcal", sa.Float, nullable=True),
        sa.Column("protein_g", sa.Float, nullable=True),
        sa.Column("carbs_g", sa.Float, nullable=True),
        sa.Column("fat_g", sa.Float, nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("user_id", "date", name="uq_energy_targets_user_date"),
    )

    # ---- plans (per-user per-day plan header) --------------------------------
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.text("0")),
        # Totals container (kcal/protein/carbs/fat) to mirror API shape
        sa.Column("totals", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("source", sa.String(32), nullable=False, server_default="heuristic"),  # heuristic | llm | import
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("user_id", "date", name="uq_plans_user_date"),
    )

    # ---- plan_meals (four slots by default: breakfast/lunch/dinner/snack) ----
    op.create_table(
        "plan_meals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("meal_type", sa.String(24), nullable=False),  # breakfast | lunch | dinner | snack
        sa.Column("title", sa.String(160), nullable=True),
        sa.Column("kcal", sa.Float, nullable=True),
        sa.Column("protein_g", sa.Float, nullable=True),
        sa.Column("carbs_g", sa.Float, nullable=True),
        sa.Column("fat_g", sa.Float, nullable=True),
        sa.Column("instructions", sa.Text, nullable=True),
        sa.Column("order_index", sa.Integer, nullable=False, server_default="0"),
        # Optional tags on a meal (e.g., "high-carb", "pre-ride", cuisine, etc.)
        sa.Column("tags", sa.JSON(), nullable=False, server_default=JSON_EMPTY_ARRAY),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
    )
    op.create_index("ix_plan_meals_plan_order", "plan_meals", ["plan_id", "order_index"], unique=False)

    # ---- plan_items (ingredients/items inside a meal) -------------------------
    op.create_table(
        "plan_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "meal_id", sa.Integer, sa.ForeignKey("plan_meals.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("qty", sa.Float, nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("kcal", sa.Float, nullable=True),
        sa.Column("protein_g", sa.Float, nullable=True),
        sa.Column("carbs_g", sa.Float, nullable=True),
        sa.Column("fat_g", sa.Float, nullable=True),
        # Per-item extra metadata (brand, UPC, substitutions, etc.)
        sa.Column("meta", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
    )

    # ---- meal_suggestions (candidate meals/plans; can store LLM output) -------
    op.create_table(
        "meal_suggestions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=True, index=True),
        sa.Column("window", sa.String(16), nullable=True),  # 3d | 5d | 7d | custom
        sa.Column("source", sa.String(16), nullable=False, server_default="llm"),  # llm | heuristic | import
        # Store the entire suggestion blob (prompt/result/choices/justification)
        sa.Column("payload", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
    )

    # ---- llm_prompts (versioned prompt templates) -----------------------------
    op.create_table(
        "llm_prompts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False, server_default=JSON_EMPTY_ARRAY),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
    )

    # ---- generation_jobs (LLM async jobs / audit) ----------------------------
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("prompt_id", sa.Integer, sa.ForeignKey("llm_prompts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),  # queued|running|succeeded|failed
        sa.Column("return_path", sa.String(255), nullable=True),  # e.g., plan id or date this applies to
        sa.Column("request", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("result", sa.JSON(), nullable=False, server_default=JSON_EMPTY_OBJECT),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("(datetime('now'))")),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("generation_jobs")
    op.drop_table("llm_prompts")
    op.drop_table("meal_suggestions")
    op.drop_table("plan_items")
    op.drop_table("plan_meals")
    op.drop_table("plans")
    op.drop_table("energy_targets")
    op.drop_table("user_preferences")
