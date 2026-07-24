import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "fd4129b081fe"
down_revision = "b2c135e8c068"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade():
    op.drop_column("users", "created_at")
