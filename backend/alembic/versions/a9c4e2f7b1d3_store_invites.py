"""store invites for employees (managers)

Revision ID: a9c4e2f7b1d3
Revises: e4b5c6d7f8a9
Create Date: 2026-10-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a9c4e2f7b1d3"
down_revision: Union[str, None] = "e4b5c6d7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_invites",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("store_owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(["store_owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["accepted_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_store_invites_token_hash"),
    )
    op.create_index(
        op.f("ix_store_invites_store_owner_user_id"),
        "store_invites",
        ["store_owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_store_invites_email"),
        "store_invites",
        ["email"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_store_invites_email"), table_name="store_invites")
    op.drop_index(op.f("ix_store_invites_store_owner_user_id"), table_name="store_invites")
    op.drop_table("store_invites")
