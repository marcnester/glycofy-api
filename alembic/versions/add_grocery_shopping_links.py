"""cache grocery shopping handoff links

Revision ID: add_grocery_shopping_links
Revises: add_grocery_preferences
"""

import sqlalchemy as sa

from alembic import op

revision = "add_grocery_shopping_links"
down_revision = "add_grocery_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("grocery_approvals", sa.Column("shopping_url", sa.Text(), nullable=True))
    op.add_column("grocery_approvals", sa.Column("shopping_fingerprint", sa.String(64), nullable=True))
    op.add_column("grocery_approvals", sa.Column("shopping_created_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("grocery_approvals", "shopping_created_at")
    op.drop_column("grocery_approvals", "shopping_fingerprint")
    op.drop_column("grocery_approvals", "shopping_url")
