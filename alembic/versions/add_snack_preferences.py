"""add flexible daily snack preferences

Revision ID: add_snack_preferences
Revises: add_beta_feedback_analytics
"""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "add_snack_preferences"
down_revision = "add_beta_feedback_analytics"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns("user_preferences")}


def upgrade() -> None:
    columns = _columns()
    if "daily_snack_count" not in columns:
        op.add_column("user_preferences", sa.Column("daily_snack_count", sa.Integer(), nullable=True))
    if "snack_times" not in columns:
        op.add_column("user_preferences", sa.Column("snack_times", sa.JSON(), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("user_preferences") as batch:
        if "snack_times" in columns:
            batch.drop_column("snack_times")
        if "daily_snack_count" in columns:
            batch.drop_column("daily_snack_count")
