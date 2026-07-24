"""add oauth_accounts table and activity source columns

Revision ID: 20251030_add_oauth_and_activity_source_cols
Revises: fd4129b081fe
Create Date: 2025-10-30 23:25:00

"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers, used by Alembic.
revision = "20251030_add_oauth_and_activity_source_cols"
down_revision = "fd4129b081fe"
branch_labels = None
depends_on = None


def upgrade():
    # ---- activities: add source columns (SQLite can ADD COLUMN) ----
    with op.batch_alter_table("activities") as batch:
        # nullable=True so SQLite ADD COLUMN works without table rewrite
        if not _has_column("activities", "source_provider"):
            batch.add_column(sa.Column("source_provider", sa.String(32), nullable=True))
        if not _has_column("activities", "source_id"):
            batch.add_column(sa.Column("source_id", sa.String(128), nullable=True))

    # backfill default provider for existing rows (optional)
    op.execute("UPDATE activities SET source_provider='manual' WHERE source_provider IS NULL")

    # ---- oauth_accounts: create if missing ----
    if not _has_table("oauth_accounts"):
        op.create_table(
            "oauth_accounts",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, nullable=False, index=True),
            sa.Column("provider", sa.String(32), nullable=False, index=True),
            sa.Column("external_athlete_id", sa.String(64), nullable=True),
            sa.Column("access_token", sa.Text, nullable=True),
            sa.Column("refresh_token", sa.Text, nullable=True),
            sa.Column("expires_at", sa.Integer, nullable=True),  # epoch seconds
            sa.Column("scope", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(datetime('now'))"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(datetime('now'))"),
                nullable=False,
            ),
            sa.UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),
        )


def downgrade():
    # Best-effort safe downgrades for SQLite
    if _has_table("oauth_accounts"):
        op.drop_table("oauth_accounts")

    # SQLite can't DROP COLUMN easily; leave columns in place.


# ---------- helpers ----------
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


def _get_bind_connection() -> Connection:
    ctx = op.get_context()
    return ctx.bind


def _has_table(name: str) -> bool:
    insp = inspect(_get_bind_connection())
    return name in insp.get_table_names()


def _has_column(table: str, column: str) -> bool:
    insp = inspect(_get_bind_connection())
    cols = [c["name"] for c in insp.get_columns(table)] if _has_table(table) else []
    return column in cols
