"""ai competitor playwright creds

Revision ID: 976e4903b0a0
Revises: e2d1c101d654
Create Date: 2026-05-10 16:39:15.695813

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "976e4903b0a0"
down_revision: Union[str, None] = "e2d1c101d654"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Encrypted WB cabinet credentials
    op.create_table(
        "ai_wb_cabinet_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("wb_login_enc", sa.Text(), nullable=False),
        sa.Column("wb_password_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status in ('active','invalid','needs_reauth','disabled')",
            name="ck_ai_wb_cabinet_credentials_status",
        ),
    )
    op.create_index("ix_ai_wb_cabinet_credentials_user_id", "ai_wb_cabinet_credentials", ["user_id"])

    # Extend competitor reports with lifecycle metadata
    op.add_column("ai_competitor_comparison_reports", sa.Column("valid_until", sa.Date(), nullable=True))
    op.add_column(
        "ai_competitor_comparison_reports",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
    )
    op.add_column(
        "ai_competitor_comparison_reports",
        sa.Column("cost_or_limit_spent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("ai_competitor_comparison_reports", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_ai_competitor_reports_status",
        "ai_competitor_comparison_reports",
        "status in ('ready','stale','running','error')",
    )

    # Audit log for explicit confirm actions
    op.create_table(
        "ai_competitor_report_actions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("ai_competitor_comparison_reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("action in ('create','refresh')", name="ck_ai_comp_report_actions_action"),
        sa.CheckConstraint("result in ('ok','error')", name="ck_ai_comp_report_actions_result"),
    )
    op.create_index("ix_ai_competitor_report_actions_user_id", "ai_competitor_report_actions", ["user_id"])
    op.create_index("ix_ai_competitor_report_actions_report_id", "ai_competitor_report_actions", ["report_id"])
    op.create_index(
        "ix_ai_competitor_report_actions_requested_at",
        "ai_competitor_report_actions",
        ["requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_competitor_report_actions_requested_at", table_name="ai_competitor_report_actions")
    op.drop_index("ix_ai_competitor_report_actions_report_id", table_name="ai_competitor_report_actions")
    op.drop_index("ix_ai_competitor_report_actions_user_id", table_name="ai_competitor_report_actions")
    op.drop_table("ai_competitor_report_actions")

    op.drop_constraint("ck_ai_competitor_reports_status", "ai_competitor_comparison_reports", type_="check")
    op.drop_column("ai_competitor_comparison_reports", "last_error")
    op.drop_column("ai_competitor_comparison_reports", "cost_or_limit_spent")
    op.drop_column("ai_competitor_comparison_reports", "status")
    op.drop_column("ai_competitor_comparison_reports", "valid_until")

    op.drop_index("ix_ai_wb_cabinet_credentials_user_id", table_name="ai_wb_cabinet_credentials")
    op.drop_table("ai_wb_cabinet_credentials")
