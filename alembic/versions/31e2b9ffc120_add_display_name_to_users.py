"""add display_name to users

Revision ID: 31e2b9ffc120
Revises: 20251030_add_oauth_and_activity_source_cols
Create Date: 2025-11-05 16:59:25.726502

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "31e2b9ffc120"
down_revision: str | Sequence[str] | None = "20251030_add_oauth_and_activity_source_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
