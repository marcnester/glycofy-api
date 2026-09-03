"""add account verification and recovery tokens

Revision ID: add_account_trust
Revises: add_weekly_planning_jobs
"""

import sqlalchemy as sa

from alembic import op

revision = "add_account_trust"
down_revision = "add_weekly_planning_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    op.create_table(
        "account_action_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="ux_account_action_token_hash"),
    )
    op.create_index("ix_account_token_user_purpose", "account_action_tokens", ["user_id", "purpose"])


def downgrade() -> None:
    op.drop_index("ix_account_token_user_purpose", table_name="account_action_tokens")
    op.drop_table("account_action_tokens")
    op.drop_column("users", "email_verified_at")
