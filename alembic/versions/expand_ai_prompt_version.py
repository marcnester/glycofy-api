"""expand AI prompt version telemetry

Revision ID: expand_ai_prompt_version
Revises: add_snack_preferences
"""

import sqlalchemy as sa

from alembic import op

revision = "expand_ai_prompt_version"
down_revision = "add_snack_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_operation_metrics") as batch:
        batch.alter_column("prompt_version", existing_type=sa.String(40), type_=sa.String(100), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("ai_operation_metrics") as batch:
        batch.alter_column("prompt_version", existing_type=sa.String(100), type_=sa.String(40), nullable=True)
