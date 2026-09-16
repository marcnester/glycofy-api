"""add structured meal preference learning

Revision ID: add_meal_preference_events
Revises: backfill_recipe_cooking_times
Create Date: 2026-09-16 12:00:00
"""

import sqlalchemy as sa

from alembic import op

revision = "add_meal_preference_events"
down_revision = "backfill_recipe_cooking_times"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_preference_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_meal_id", sa.Integer(), nullable=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("meal_type", sa.String(length=24), nullable=False),
        sa.Column("meal_title", sa.String(length=160), nullable=False),
        sa.Column("signal", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_meal_id"], ["plan_meals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_preference_events_plan_meal_id", "meal_preference_events", ["plan_meal_id"])
    op.create_index("ix_meal_preference_events_user_id", "meal_preference_events", ["user_id"])
    op.create_index("ix_meal_preference_user_created", "meal_preference_events", ["user_id", "created_at"])
    op.create_index("ix_meal_preference_user_signal", "meal_preference_events", ["user_id", "signal"])


def downgrade() -> None:
    op.drop_index("ix_meal_preference_user_signal", table_name="meal_preference_events")
    op.drop_index("ix_meal_preference_user_created", table_name="meal_preference_events")
    op.drop_index("ix_meal_preference_events_user_id", table_name="meal_preference_events")
    op.drop_index("ix_meal_preference_events_plan_meal_id", table_name="meal_preference_events")
    op.drop_table("meal_preference_events")
