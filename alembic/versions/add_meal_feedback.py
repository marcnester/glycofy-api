"""add adaptive meal feedback

Revision ID: add_meal_feedback
Revises: add_grocery_approvals
Create Date: 2026-09-02 22:45:00
"""

import sqlalchemy as sa

from alembic import op

revision = "add_meal_feedback"
down_revision = "add_grocery_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_meal_id", sa.Integer(), nullable=True),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("meal_type", sa.String(length=24), nullable=False),
        sa.Column("meal_title", sa.String(length=160), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("portion", sa.String(length=24), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("hunger_after", sa.String(length=24), nullable=True),
        sa.Column("energy_after", sa.String(length=24), nullable=True),
        sa.Column("digestion", sa.String(length=24), nullable=True),
        sa.Column("practicality", sa.String(length=24), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_meal_id"], ["plan_meals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "plan_meal_id", name="ux_meal_feedback_user_meal"),
    )
    op.create_index("ix_meal_feedback_plan_meal_id", "meal_feedback", ["plan_meal_id"])
    op.create_index("ix_meal_feedback_user_date", "meal_feedback", ["user_id", "plan_date"])
    op.create_index("ix_meal_feedback_user_id", "meal_feedback", ["user_id"])
    op.create_index("ix_meal_feedback_user_updated", "meal_feedback", ["user_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_meal_feedback_user_updated", table_name="meal_feedback")
    op.drop_index("ix_meal_feedback_user_id", table_name="meal_feedback")
    op.drop_index("ix_meal_feedback_user_date", table_name="meal_feedback")
    op.drop_index("ix_meal_feedback_plan_meal_id", table_name="meal_feedback")
    op.drop_table("meal_feedback")
