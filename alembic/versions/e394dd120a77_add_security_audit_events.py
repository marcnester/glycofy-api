"""add security audit events

Revision ID: e394dd120a77
Revises: ca65d23af381
Create Date: 2026-08-10
"""

import sqlalchemy as sa

from alembic import op

revision = "e394dd120a77"
down_revision = "ca65d23af381"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("client_id_hash", sa.String(length=64), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_audit_occurred", "security_audit_events", ["occurred_at"])
    op.create_index("ix_security_audit_type_outcome", "security_audit_events", ["event_type", "outcome"])
    op.create_index("ix_security_audit_user_time", "security_audit_events", ["user_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_security_audit_user_time", table_name="security_audit_events")
    op.drop_index("ix_security_audit_type_outcome", table_name="security_audit_events")
    op.drop_index("ix_security_audit_occurred", table_name="security_audit_events")
    op.drop_table("security_audit_events")
