"""Store remaining pantry and purchased quantities."""

import sqlalchemy as sa

from alembic import op

revision = "add_grocery_stock"
down_revision = "add_workout_fuel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("grocery_preferences", sa.Column("pantry_quantity", sa.Float(), nullable=True))
    op.add_column("grocery_preferences", sa.Column("purchased_quantity", sa.Float(), nullable=True))
    op.add_column("grocery_preferences", sa.Column("stock_unit", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("grocery_preferences", "stock_unit")
    op.drop_column("grocery_preferences", "purchased_quantity")
    op.drop_column("grocery_preferences", "pantry_quantity")
