"""Store carbohydrate consumed during completed workouts."""

import sqlalchemy as sa

from alembic import op

revision = "add_workout_fuel"
down_revision = "add_kroger_cart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("during_carbs_g", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "during_carbs_g")
