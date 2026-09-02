"""add activities created_at column

Revision ID: add_activity_created_at
Revises: e394dd120a77
Create Date: 2026-09-01 17:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "add_activity_created_at"
down_revision: str | Sequence[str] | None = "e394dd120a77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("activities")}
    if "created_at" not in columns:
        op.add_column("activities", sa.Column("created_at", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("activities")}
    if "created_at" in columns:
        with op.batch_alter_table("activities") as batch:
            batch.drop_column("created_at")
