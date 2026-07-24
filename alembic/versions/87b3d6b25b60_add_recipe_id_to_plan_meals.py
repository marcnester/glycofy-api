"""add recipe_id to plan_meals

Revision ID: 87b3d6b25b60
Revises: d997774387f6
Create Date: 2025-12-16
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "87b3d6b25b60"
down_revision = "d997774387f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_meals") as batch_op:
        batch_op.add_column(sa.Column("recipe_id", sa.Integer(), nullable=True))
        # Optional: index helps lookups if you query by recipe_id
        batch_op.create_index("ix_plan_meals_recipe_id", ["recipe_id"])


def downgrade() -> None:
    with op.batch_alter_table("plan_meals") as batch_op:
        batch_op.drop_index("ix_plan_meals_recipe_id")
        batch_op.drop_column("recipe_id")
