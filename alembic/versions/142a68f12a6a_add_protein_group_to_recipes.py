"""add protein_group to recipes

Revision ID: 142a68f12a6a
Revises: 87b3d6b25b60
Create Date: 2025-12-17 20:53:49.509939

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "142a68f12a6a"
down_revision: str | Sequence[str] | None = "87b3d6b25b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite-safe migration (batch mode)
    with op.batch_alter_table("recipes") as batch_op:
        batch_op.add_column(sa.Column("protein_group", sa.String(length=32), nullable=True))
        batch_op.create_index("ix_recipes_protein_group", ["protein_group"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("recipes") as batch_op:
        batch_op.drop_index("ix_recipes_protein_group")
        batch_op.drop_column("protein_group")
