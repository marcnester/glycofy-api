"""add weekly job recovery state and AI operational metrics

Revision ID: add_production_resilience
Revises: add_account_trust
"""

import sqlalchemy as sa

from alembic import op

revision = "add_production_resilience"
down_revision = "add_account_trust"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("weekly_planning_jobs") as batch_op:
        batch_op.add_column(sa.Column("error_code", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("error_reference", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("worker_id", sa.String(64), nullable=True))
    op.create_table(
        "ai_operation_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("prompt_version", sa.String(40), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("accepted_items", sa.Integer(), nullable=True),
        sa.Column("rejected_items", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
    )
    op.create_index("ix_ai_metric_occurred", "ai_operation_metrics", ["occurred_at"])
    op.create_index("ix_ai_metric_operation_status", "ai_operation_metrics", ["operation", "status"])


def downgrade() -> None:
    op.drop_index("ix_ai_metric_operation_status", table_name="ai_operation_metrics")
    op.drop_index("ix_ai_metric_occurred", table_name="ai_operation_metrics")
    op.drop_table("ai_operation_metrics")
    with op.batch_alter_table("weekly_planning_jobs") as batch_op:
        batch_op.drop_column("worker_id")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("error_reference")
        batch_op.drop_column("error_code")
