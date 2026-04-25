"""funnel rolling sync state for 7-day tail repair

Revision ID: f2a3b4c5d6e7
Revises: b7c8d9e0f1a2
Create Date: 2026-04-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "funnel_rolling_sync_state",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="idle", nullable=False),
        sa.Column("last_completed_date", sa.Date(), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_funnel_rolling_user"),
    )
    op.create_index(
        "ix_funnel_rolling_sync_state_user_id",
        "funnel_rolling_sync_state",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_funnel_rolling_sync_state_user_id", table_name="funnel_rolling_sync_state")
    op.drop_table("funnel_rolling_sync_state")

