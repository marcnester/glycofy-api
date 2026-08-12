"""add user token version for session revocation

Revision ID: ca65d23af381
Revises: 4f33c95c9c8b
Create Date: 2026-08-10
"""

import sqlalchemy as sa

from alembic import op

revision = "ca65d23af381"
down_revision = "4f33c95c9c8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("token_version", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("token_version")
