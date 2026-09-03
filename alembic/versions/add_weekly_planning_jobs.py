"""add durable weekly planning jobs

Revision ID: add_weekly_planning_jobs
Revises: add_grocery_shopping_links
"""

import sqlalchemy as sa

from alembic import op

revision = "add_weekly_planning_jobs"
down_revision = "add_grocery_shopping_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_planning_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("message", sa.String(240), nullable=False),
        sa.Column("completed_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_days", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_weekly_job_user_status", "weekly_planning_jobs", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_weekly_job_user_status", table_name="weekly_planning_jobs")
    op.drop_table("weekly_planning_jobs")
