"""finance missing sync state (dedup ranges)

Revision ID: 1f2e3d4c5b6a
Revises: 8c4f5a1d2b3c
Create Date: 2026-04-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1f2e3d4c5b6a"
down_revision: Union[str, None] = "8c4f5a1d2b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "finance_missing_sync_state",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="idle", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_http_code", sa.Integer(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date_from", "date_to", name="uq_finance_missing_user_range"),
    )
    op.create_index(
        "ix_finance_missing_sync_state_user_id",
        "finance_missing_sync_state",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_finance_missing_sync_state_user_id", table_name="finance_missing_sync_state")
    op.drop_table("finance_missing_sync_state")

