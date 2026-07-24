"""add display_name_and_units_to_users

Revision ID: 749ef13f8070
Revises: 20251106_01
Create Date: 2025-11-18 15:47:18.127267

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "749ef13f8070"
down_revision: str | Sequence[str] | None = "20251106_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
