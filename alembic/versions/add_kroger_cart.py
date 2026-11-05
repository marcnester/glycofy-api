"""Add isolated Kroger authorization and cart review records."""

import sqlalchemy as sa

from alembic import op

revision = "add_kroger_cart"
down_revision = "add_stateful_sessions_passkeys"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kroger_connections",
        sa.Column("connection_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
    )
    op.create_table(
        "kroger_authorizations",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "kroger_cart_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), unique=True, nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("kroger_cart_reviews")
    op.drop_table("kroger_authorizations")
    op.drop_table("kroger_connections")
