"""Add Ozon-only tax settings and dated operating expenses.

Revision ID: d8e9f0a1b2c3
Revises: b4c5d6e7f809
Create Date: 2026-10-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "b4c5d6e7f809"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ozon_finance_settings",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "operation_expenses_confirmed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_ozon_finance_settings_user_id", "ozon_finance_settings", ["user_id"], unique=False)

    op.create_table(
        "ozon_operational_expenses",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_ozon_operational_expense_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uq_ozon_operational_expense_user_date"),
    )
    op.create_index("ix_ozon_operational_expenses_user_id", "ozon_operational_expenses", ["user_id"], unique=False)
    op.create_index(
        "ix_ozon_operational_expense_user_date",
        "ozon_operational_expenses",
        ["user_id", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ozon_operational_expense_user_date", table_name="ozon_operational_expenses")
    op.drop_index("ix_ozon_operational_expenses_user_id", table_name="ozon_operational_expenses")
    op.drop_table("ozon_operational_expenses")
    op.drop_index("ix_ozon_finance_settings_user_id", table_name="ozon_finance_settings")
    op.drop_table("ozon_finance_settings")
