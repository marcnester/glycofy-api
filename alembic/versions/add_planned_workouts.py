"""add provider-neutral planned workouts

Revision ID: add_planned_workouts
Revises: add_user_pref_columns
Create Date: 2026-09-02 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "add_planned_workouts"
down_revision: str | Sequence[str] | None = "add_user_pref_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "planned_workouts" in inspector.get_table_names():
        return
    op.create_table(
        "planned_workouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=True),
        sa.Column("sport", sa.String(length=32), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("intensity", sa.String(length=16), nullable=False),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("priority", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "source", "external_id", name="ux_planned_workout_source"),
    )
    op.create_index("ix_planned_workouts_user_date", "planned_workouts", ["user_id", "workout_date"])


def downgrade() -> None:
    if "planned_workouts" in inspect(op.get_bind()).get_table_names():
        op.drop_table("planned_workouts")
