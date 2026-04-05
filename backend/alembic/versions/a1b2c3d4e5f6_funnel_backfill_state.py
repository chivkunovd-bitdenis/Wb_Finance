"""funnel backfill state for YTD products API

Revision ID: a1b2c3d4e5f6
Revises: 9625e2645820
Create Date: 2026-03-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9625e2645820"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "funnel_backfill_state",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("calendar_year", sa.Integer(), nullable=False),
        sa.Column("last_completed_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="idle", nullable=False),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "calendar_year", name="uq_funnel_backfill_user_year"),
    )
    op.create_index("ix_funnel_backfill_state_user_id", "funnel_backfill_state", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_funnel_backfill_state_user_id", table_name="funnel_backfill_state")
    op.drop_table("funnel_backfill_state")
