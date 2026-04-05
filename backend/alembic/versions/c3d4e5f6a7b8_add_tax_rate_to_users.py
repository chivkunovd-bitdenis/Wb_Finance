"""add tax_rate to users

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-04-05

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "tax_rate",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0.0600",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "tax_rate")
