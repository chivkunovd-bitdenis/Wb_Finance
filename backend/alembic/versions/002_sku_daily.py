"""Add sku_daily mart (user x date x nm_id) for fast time-series by article

Revision ID: 002
Revises: 001
Create Date: 2026-03-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sku_daily",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("commission", sa.Numeric(14, 2), nullable=True),
        sa.Column("logistics", sa.Numeric(14, 2), nullable=True),
        sa.Column("penalties", sa.Numeric(14, 2), nullable=True),
        sa.Column("storage", sa.Numeric(14, 2), nullable=True),
        sa.Column("ads_spend", sa.Numeric(14, 2), nullable=True),
        sa.Column("cogs", sa.Numeric(14, 2), nullable=True),
        sa.Column("tax", sa.Numeric(14, 2), nullable=True),
        sa.Column("margin", sa.Numeric(14, 2), nullable=True),
        sa.Column("open_count", sa.Integer(), nullable=True),
        sa.Column("cart_count", sa.Integer(), nullable=True),
        sa.Column("order_count", sa.Integer(), nullable=True),
        sa.Column("order_sum", sa.Numeric(14, 2), nullable=True),
    )
    op.create_index("ix_sku_daily_user_id", "sku_daily", ["user_id"], unique=False)
    op.create_index("ix_sku_daily_user_date", "sku_daily", ["user_id", "date"], unique=False)
    op.create_unique_constraint("uq_sku_daily_user_date_nm", "sku_daily", ["user_id", "date", "nm_id"])


def downgrade() -> None:
    op.drop_table("sku_daily")
