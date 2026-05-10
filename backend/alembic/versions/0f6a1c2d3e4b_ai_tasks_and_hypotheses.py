"""AI module: tasks and hypotheses tables

Revision ID: 0f6a1c2d3e4b
Revises: bae96ffe0cd1
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0f6a1c2d3e4b"
down_revision = "bae96ffe0cd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("threshold", postgresql.JSONB(), nullable=True),
        sa.Column("current_value", postgresql.JSONB(), nullable=True),
        sa.Column("competitor_median_value", postgresql.JSONB(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('new', 'in_progress', 'completed', 'cancelled')", name="ck_ai_tasks_status"),
        sa.CheckConstraint("priority >= 0", name="ck_ai_tasks_priority_nonneg"),
    )
    op.create_index("ix_ai_tasks_user_id", "ai_tasks", ["user_id"])
    op.create_index("ix_ai_tasks_nm_id", "ai_tasks", ["nm_id"])
    op.create_index("ix_ai_tasks_task_type", "ai_tasks", ["task_type"])
    op.create_index("ix_ai_tasks_created_at", "ai_tasks", ["created_at"])

    op.create_table(
        "ai_hypotheses",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=True),
        sa.Column("hypothesis_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("trigger_reason", sa.Text(), nullable=True),
        sa.Column("baseline_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("competitor_median_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("expected_effect", postgresql.JSONB(), nullable=True),
        sa.Column("test_period_days", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daily_log", postgresql.JSONB(), nullable=True),
        sa.Column("result_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('draft', 'running', 'finished', 'cancelled')", name="ck_ai_hypotheses_status"),
        sa.CheckConstraint("test_period_days is null or test_period_days > 0", name="ck_ai_hypotheses_period_pos"),
    )
    op.create_index("ix_ai_hypotheses_user_id", "ai_hypotheses", ["user_id"])
    op.create_index("ix_ai_hypotheses_nm_id", "ai_hypotheses", ["nm_id"])
    op.create_index("ix_ai_hypotheses_hypothesis_type", "ai_hypotheses", ["hypothesis_type"])
    op.create_index("ix_ai_hypotheses_created_at", "ai_hypotheses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_hypotheses_created_at", table_name="ai_hypotheses")
    op.drop_index("ix_ai_hypotheses_hypothesis_type", table_name="ai_hypotheses")
    op.drop_index("ix_ai_hypotheses_nm_id", table_name="ai_hypotheses")
    op.drop_index("ix_ai_hypotheses_user_id", table_name="ai_hypotheses")
    op.drop_table("ai_hypotheses")

    op.drop_index("ix_ai_tasks_created_at", table_name="ai_tasks")
    op.drop_index("ix_ai_tasks_task_type", table_name="ai_tasks")
    op.drop_index("ix_ai_tasks_nm_id", table_name="ai_tasks")
    op.drop_index("ix_ai_tasks_user_id", table_name="ai_tasks")
    op.drop_table("ai_tasks")

