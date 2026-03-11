"""Initial tables: users, articles, raw_sales, raw_ads, pnl_daily, funnel_daily

Revision ID: 001
Revises:
Create Date: 2026-03-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("wb_api_key", sa.String(512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "articles",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("vendor_code", sa.String(255), nullable=True),
        sa.Column("name", sa.String(1000), nullable=True),
        sa.Column("subject_name", sa.String(500), nullable=True),
        sa.Column("cost_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_articles_user_id", "articles", ["user_id"], unique=False)
    op.create_unique_constraint("uq_articles_user_nm", "articles", ["user_id", "nm_id"])

    op.create_table(
        "raw_sales",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_type", sa.String(100), nullable=True),
        sa.Column("retail_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("ppvz_for_pay", sa.Numeric(14, 2), nullable=True),
        sa.Column("delivery_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("penalty", sa.Numeric(14, 2), nullable=True),
        sa.Column("additional_payment", sa.Numeric(14, 2), nullable=True),
        sa.Column("storage_fee", sa.Numeric(14, 2), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
    )
    op.create_index("ix_raw_sales_user_id", "raw_sales", ["user_id"], unique=False)
    op.create_index("ix_raw_sales_user_date", "raw_sales", ["user_id", "date"], unique=False)

    op.create_table(
        "raw_ads",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("campaign_id", sa.BigInteger(), nullable=True),
        sa.Column("spend", sa.Numeric(14, 2), nullable=True),
    )
    op.create_index("ix_raw_ads_user_id", "raw_ads", ["user_id"], unique=False)
    op.create_index("ix_raw_ads_user_date", "raw_ads", ["user_id", "date"], unique=False)

    op.create_table(
        "pnl_daily",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("commission", sa.Numeric(14, 2), nullable=True),
        sa.Column("logistics", sa.Numeric(14, 2), nullable=True),
        sa.Column("penalties", sa.Numeric(14, 2), nullable=True),
        sa.Column("storage", sa.Numeric(14, 2), nullable=True),
        sa.Column("ads_spend", sa.Numeric(14, 2), nullable=True),
        sa.Column("cogs", sa.Numeric(14, 2), nullable=True),
        sa.Column("tax", sa.Numeric(14, 2), nullable=True),
        sa.Column("margin", sa.Numeric(14, 2), nullable=True),
    )
    op.create_index("ix_pnl_daily_user_id", "pnl_daily", ["user_id"], unique=False)
    op.create_unique_constraint("uq_pnl_daily_user_date", "pnl_daily", ["user_id", "date"])

    op.create_table(
        "funnel_daily",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("vendor_code", sa.String(255), nullable=True),
        sa.Column("open_count", sa.Integer(), nullable=True),
        sa.Column("cart_count", sa.Integer(), nullable=True),
        sa.Column("order_count", sa.Integer(), nullable=True),
        sa.Column("order_sum", sa.Numeric(14, 2), nullable=True),
        sa.Column("buyout_percent", sa.Numeric(8, 2), nullable=True),
        sa.Column("cr_to_cart", sa.Numeric(8, 4), nullable=True),
        sa.Column("cr_to_order", sa.Numeric(8, 4), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_funnel_daily_user_id", "funnel_daily", ["user_id"], unique=False)
    op.create_unique_constraint("uq_funnel_daily_user_date_nm", "funnel_daily", ["user_id", "date", "nm_id"])


def downgrade() -> None:
    op.drop_table("funnel_daily")
    op.drop_table("pnl_daily")
    op.drop_table("raw_ads")
    op.drop_table("raw_sales")
    op.drop_table("articles")
    op.drop_table("users")
