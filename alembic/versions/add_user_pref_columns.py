"""add explicit user preference columns

Revision ID: add_user_pref_columns
Revises: add_activity_created_at
Create Date: 2026-09-01 20:15:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "add_user_pref_columns"
down_revision: str | Sequence[str] | None = "add_activity_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names() -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns("user_preferences")}


def upgrade() -> None:
    columns = _column_names()
    if "ingredient_exclusions" not in columns:
        op.add_column("user_preferences", sa.Column("ingredient_exclusions", sa.String(), nullable=True))
    if "diet_type" not in columns:
        op.add_column("user_preferences", sa.Column("diet_type", sa.String(), nullable=True))


def downgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("user_preferences") as batch:
        if "diet_type" in columns:
            batch.drop_column("diet_type")
        if "ingredient_exclusions" in columns:
            batch.drop_column("ingredient_exclusions")
