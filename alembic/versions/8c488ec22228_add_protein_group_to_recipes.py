"""add protein_group to recipes

Revision ID: 8c488ec22228
Revises: 142a68f12a6a
Create Date: 2025-12-16

"""

# revision identifiers, used by Alembic.
revision = "8c488ec22228"
down_revision = "142a68f12a6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: revision 142a68f12a6a already added this column and index."""


def downgrade() -> None:
    """No-op: revision 142a68f12a6a owns removal of this column and index."""
