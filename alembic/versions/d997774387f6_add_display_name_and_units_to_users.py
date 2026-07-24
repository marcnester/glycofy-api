"""add display_name and units to users

Revision ID: d997774387f6        # <-- use the ID Alembic generated
Revises: 749ef13f8070            # <-- previous revision Alembic put here
Create Date: 2025-11-18 00:00:00

"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers, used by Alembic.
revision = "d997774387f6"  # <-- keep whatever Alembic created
down_revision = "749ef13f8070"  # <-- keep whatever Alembic created
branch_labels = None
depends_on = None


def upgrade():
    # Add new nullable columns to users
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("units", sa.String(length=16), nullable=True),
    )


def downgrade():
    # Reverse the upgrade
    op.drop_column("users", "units")
    op.drop_column("users", "display_name")
