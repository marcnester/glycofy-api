"""deduplicate plan meal slots and enforce one row per slot

Revision ID: add_unique_plan_meal_slots
Revises: add_nutrition_validation_catalog
"""

import sqlalchemy as sa

from alembic import op

revision = "add_unique_plan_meal_slots"
down_revision = "add_nutrition_validation_catalog"
branch_labels = None
depends_on = None


def _indexes() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("plan_meals")}


def upgrade() -> None:
    # Prefer the row containing an actual generated meal, then the newest row.
    # This repairs historical blank snack_2 placeholders before uniqueness is
    # enforced. The window-function form works in PostgreSQL and SQLite.
    op.execute(
        sa.text(
            """
            DELETE FROM plan_meals
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY plan_id, meal_type
                               ORDER BY
                                   CASE WHEN COALESCE(kcal, 0) > 0 THEN 1 ELSE 0 END DESC,
                                   CASE WHEN NULLIF(TRIM(title), '') IS NOT NULL
                                             AND LOWER(TRIM(title)) NOT IN
                                                 (LOWER(meal_type), REPLACE(LOWER(meal_type), '_', ' '), 'snack')
                                        THEN 1 ELSE 0 END DESC,
                                   CASE WHEN NULLIF(TRIM(instructions), '') IS NOT NULL THEN 1 ELSE 0 END DESC,
                                   updated_at DESC,
                                   id DESC
                           ) AS duplicate_rank
                    FROM plan_meals
                ) ranked
                WHERE duplicate_rank > 1
            )
            """
        )
    )
    if "ux_plan_meal_slot" not in _indexes():
        op.create_index("ux_plan_meal_slot", "plan_meals", ["plan_id", "meal_type"], unique=True)


def downgrade() -> None:
    if "ux_plan_meal_slot" in _indexes():
        op.drop_index("ux_plan_meal_slot", table_name="plan_meals")
