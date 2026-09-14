"""persist reusable recipe cooking times

Revision ID: add_recipe_cooking_times
Revises: add_unique_plan_meal_slots
"""

import sqlalchemy as sa

from alembic import op

revision = "add_recipe_cooking_times"
down_revision = "add_unique_plan_meal_slots"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("recipes")}


def upgrade() -> None:
    columns = _columns()
    for name in ("prep_time_min", "cook_time_min", "total_time_min"):
        if name not in columns:
            op.add_column("recipes", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("recipes") as batch:
        for name in ("total_time_min", "cook_time_min", "prep_time_min"):
            if name in columns:
                batch.drop_column(name)
