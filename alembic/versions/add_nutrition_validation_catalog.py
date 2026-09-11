"""add persistent nutrition catalog and validation queue

Revision ID: add_nutrition_validation_catalog
Revises: expand_ai_prompt_version
"""

import sqlalchemy as sa

from alembic import op

revision = "add_nutrition_validation_catalog"
down_revision = "expand_ai_prompt_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("nutrition_catalog_entries"):
        op.create_table(
            "nutrition_catalog_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("query_key", sa.String(240), nullable=False),
            sa.Column("fdc_id", sa.Integer(), nullable=False),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("data_type", sa.String(40), nullable=False),
            sa.Column("nutrients_per_100g", sa.JSON(), nullable=False),
            sa.Column("source", sa.String(32), nullable=False, server_default="usda_fdc"),
            sa.Column("verified_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("query_key", name="ux_nutrition_catalog_query_key"),
        )
        op.create_index("ix_nutrition_catalog_fdc_id", "nutrition_catalog_entries", ["fdc_id"])

    inspector = sa.inspect(bind)
    if not inspector.has_table("nutrition_validation_jobs"):
        op.create_table(
            "nutrition_validation_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("query_key", sa.String(240), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error_code", sa.String(80), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("query_key", name="ux_nutrition_validation_query_key"),
        )
        op.create_index(
            "ix_nutrition_validation_status_due",
            "nutrition_validation_jobs",
            ["status", "next_attempt_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("nutrition_validation_jobs"):
        op.drop_index("ix_nutrition_validation_status_due", table_name="nutrition_validation_jobs")
        op.drop_table("nutrition_validation_jobs")
    inspector = sa.inspect(bind)
    if inspector.has_table("nutrition_catalog_entries"):
        op.drop_index("ix_nutrition_catalog_fdc_id", table_name="nutrition_catalog_entries")
        op.drop_table("nutrition_catalog_entries")
