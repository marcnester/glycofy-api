"""add package and pantry grocery preferences

Revision ID: add_grocery_preferences
Revises: add_meal_feedback
Create Date: 2026-09-02 23:00:00
"""

import sqlalchemy as sa

from alembic import op

revision = "add_grocery_preferences"
down_revision = "add_meal_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grocery_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ingredient_key", sa.String(length=200), nullable=False),
        sa.Column("in_pantry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("preferred_brand", sa.String(length=120), nullable=True),
        sa.Column("package_quantity", sa.Float(), nullable=True),
        sa.Column("package_unit", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ingredient_key", name="ux_grocery_preference_user_ingredient"),
    )
    op.create_index("ix_grocery_preference_user", "grocery_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_grocery_preference_user", table_name="grocery_preferences")
    op.drop_table("grocery_preferences")
