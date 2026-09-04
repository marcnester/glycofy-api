"""add beta feedback and privacy-safe product analytics

Revision ID: add_beta_feedback_analytics
Revises: add_production_resilience
"""

import sqlalchemy as sa

from alembic import op

revision = "add_beta_feedback_analytics"
down_revision = "add_production_resilience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "beta_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(1200), nullable=False),
        sa.Column("page_path", sa.String(160), nullable=False),
        sa.Column("browser_family", sa.String(24), nullable=False),
        sa.Column("viewport", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("related_request_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_beta_feedback_created_status", "beta_feedback", ["created_at", "status"])
    op.create_table(
        "product_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_name", sa.String(48), nullable=False),
        sa.Column("page_path", sa.String(160), nullable=False),
        sa.Column("browser_family", sa.String(24), nullable=False),
        sa.Column("viewport", sa.String(16), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_product_event_occurred_name", "product_events", ["occurred_at", "event_name"])
    op.create_index("ix_product_event_user_occurred", "product_events", ["user_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_product_event_user_occurred", table_name="product_events")
    op.drop_index("ix_product_event_occurred_name", table_name="product_events")
    op.drop_table("product_events")
    op.drop_index("ix_beta_feedback_created_status", table_name="beta_feedback")
    op.drop_table("beta_feedback")
