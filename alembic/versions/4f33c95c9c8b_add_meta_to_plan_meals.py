"""add meta to plan meals

Revision ID: 4f33c95c9c8b
Revises: 8c488ec22228
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "4f33c95c9c8b"
down_revision = "8c488ec22228"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_meals", sa.Column("meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plan_meals") as batch_op:
        batch_op.drop_column("meta")
