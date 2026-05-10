"""AI module: hypothesis daily log + fingerprints

Revision ID: 1b2c3d4e5f60
Revises: 0f6a1c2d3e4b
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "1b2c3d4e5f60"
down_revision = "0f6a1c2d3e4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_tasks", sa.Column("fingerprint", sa.String(length=80), nullable=True))
    op.create_index("ix_ai_tasks_fingerprint", "ai_tasks", ["fingerprint"])
    op.create_unique_constraint("uq_ai_tasks_user_fingerprint", "ai_tasks", ["user_id", "fingerprint"])

    op.add_column("ai_hypotheses", sa.Column("fingerprint", sa.String(length=80), nullable=True))
    op.create_index("ix_ai_hypotheses_fingerprint", "ai_hypotheses", ["fingerprint"])
    op.create_unique_constraint("uq_ai_hypotheses_user_fingerprint", "ai_hypotheses", ["user_id", "fingerprint"])

    op.create_table(
        "ai_hypothesis_daily_log",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "hypothesis_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("ai_hypotheses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("happened", sa.Text(), nullable=True),
        sa.Column("changed", sa.Text(), nullable=True),
        sa.Column("unchanged", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("hypothesis_id", "day", name="uq_ai_hypothesis_daily_log_hyp_day"),
    )
    op.create_index("ix_ai_hypothesis_daily_log_hypothesis_id", "ai_hypothesis_daily_log", ["hypothesis_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_hypothesis_daily_log_hypothesis_id", table_name="ai_hypothesis_daily_log")
    op.drop_table("ai_hypothesis_daily_log")

    op.drop_constraint("uq_ai_hypotheses_user_fingerprint", "ai_hypotheses", type_="unique")
    op.drop_index("ix_ai_hypotheses_fingerprint", table_name="ai_hypotheses")
    op.drop_column("ai_hypotheses", "fingerprint")

    op.drop_constraint("uq_ai_tasks_user_fingerprint", "ai_tasks", type_="unique")
    op.drop_index("ix_ai_tasks_fingerprint", table_name="ai_tasks")
    op.drop_column("ai_tasks", "fingerprint")

