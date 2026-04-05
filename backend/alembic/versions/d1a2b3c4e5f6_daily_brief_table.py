"""daily_brief table for AI daily summaries

Revision ID: d1a2b3c4e5f6
Revises: b7c8d9e0f1a2, ca01bc007d73
Create Date: 2026-03-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d1a2b3c4e5f6"
down_revision: Union[str, tuple[str, ...]] = ("b7c8d9e0f1a2", "ca01bc007d73")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_brief",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date_for", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date_for", name="uq_daily_brief_user_date"),
    )
    op.create_index("ix_daily_brief_user_id", "daily_brief", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_daily_brief_user_id", table_name="daily_brief")
    op.drop_table("daily_brief")
