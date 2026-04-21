"""monthly plan table

Revision ID: 7b1edcbb9b66
Revises: c3d4e5f6a7b8
Create Date: 2026-04-21 14:56:44.969810

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7b1edcbb9b66'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monthly_plan",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "month", "metric_key", name="uq_monthly_plan_user_month_metric"),
    )
    op.create_index(op.f("ix_monthly_plan_user_id"), "monthly_plan", ["user_id"], unique=False)
    op.create_index(op.f("ix_monthly_plan_month"), "monthly_plan", ["month"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_monthly_plan_month"), table_name="monthly_plan")
    op.drop_index(op.f("ix_monthly_plan_user_id"), table_name="monthly_plan")
    op.drop_table("monthly_plan")
