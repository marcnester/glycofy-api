"""add grocery approval snapshots

Revision ID: add_grocery_approvals
Revises: add_planned_workouts
Create Date: 2026-09-02 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "add_grocery_approvals"
down_revision: str | Sequence[str] | None = "add_planned_workouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "grocery_approvals" in inspector.get_table_names():
        return
    op.create_table(
        "grocery_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("servings", sa.Integer(), server_default="1", nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("plan_fingerprint", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "start_date", "end_date", name="ux_grocery_approval_user_range"),
    )
    op.create_index(
        "ix_grocery_approval_user_range",
        "grocery_approvals",
        ["user_id", "start_date", "end_date"],
    )


def downgrade() -> None:
    if "grocery_approvals" in inspect(op.get_bind()).get_table_names():
        op.drop_table("grocery_approvals")
