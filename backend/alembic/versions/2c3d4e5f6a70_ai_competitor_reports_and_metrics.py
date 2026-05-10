"""AI module: competitor comparison reports (manual import)

Revision ID: 2c3d4e5f6a70
Revises: 1b2c3d4e5f60
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2c3d4e5f6a70"
down_revision = "1b2c3d4e5f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_competitor_comparison_reports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "report_date", "period", name="uq_ai_competitor_report_user_date_period"),
    )
    op.create_index(
        "ix_ai_competitor_comparison_reports_user_id",
        "ai_competitor_comparison_reports",
        ["user_id"],
    )
    op.create_index(
        "ix_ai_competitor_comparison_reports_report_date",
        "ai_competitor_comparison_reports",
        ["report_date"],
    )
    op.create_index(
        "ix_ai_competitor_comparison_reports_created_at",
        "ai_competitor_comparison_reports",
        ["created_at"],
    )

    op.create_table(
        "ai_competitor_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("ai_competitor_comparison_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.Column("metric_code", sa.String(length=32), nullable=False),
        sa.Column("our_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("competitor_median_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("report_id", "nm_id", "metric_code", name="uq_ai_comp_metric_report_nm_code"),
    )
    op.create_index("ix_ai_competitor_metrics_report_id", "ai_competitor_metrics", ["report_id"])
    op.create_index("ix_ai_competitor_metrics_nm_id", "ai_competitor_metrics", ["nm_id"])
    op.create_index("ix_ai_competitor_metrics_metric_code", "ai_competitor_metrics", ["metric_code"])


def downgrade() -> None:
    op.drop_index("ix_ai_competitor_metrics_metric_code", table_name="ai_competitor_metrics")
    op.drop_index("ix_ai_competitor_metrics_nm_id", table_name="ai_competitor_metrics")
    op.drop_index("ix_ai_competitor_metrics_report_id", table_name="ai_competitor_metrics")
    op.drop_table("ai_competitor_metrics")

    op.drop_index(
        "ix_ai_competitor_comparison_reports_created_at",
        table_name="ai_competitor_comparison_reports",
    )
    op.drop_index(
        "ix_ai_competitor_comparison_reports_report_date",
        table_name="ai_competitor_comparison_reports",
    )
    op.drop_index(
        "ix_ai_competitor_comparison_reports_user_id",
        table_name="ai_competitor_comparison_reports",
    )
    op.drop_table("ai_competitor_comparison_reports")

