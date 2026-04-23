"""store access grants and audit

Revision ID: 8c4f5a1d2b3c
Revises: 7b1edcbb9b66
Create Date: 2026-04-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "8c4f5a1d2b3c"
down_revision: Union[str, None] = "7b1edcbb9b66"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("store_owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("viewer_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["store_owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["viewer_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "store_owner_user_id",
            "viewer_user_id",
            name="uq_store_access_grant_owner_viewer",
        ),
    )
    op.create_index(
        op.f("ix_store_access_grants_store_owner_user_id"),
        "store_access_grants",
        ["store_owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_store_access_grants_viewer_user_id"),
        "store_access_grants",
        ["viewer_user_id"],
        unique=False,
    )

    op.create_table(
        "store_access_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("store_owner_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("viewer_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["store_owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["viewer_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_store_access_audit_events_store_owner_user_id"),
        "store_access_audit_events",
        ["store_owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_store_access_audit_events_viewer_user_id"),
        "store_access_audit_events",
        ["viewer_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_store_access_audit_events_actor_user_id"),
        "store_access_audit_events",
        ["actor_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_store_access_audit_events_actor_user_id"), table_name="store_access_audit_events")
    op.drop_index(op.f("ix_store_access_audit_events_viewer_user_id"), table_name="store_access_audit_events")
    op.drop_index(op.f("ix_store_access_audit_events_store_owner_user_id"), table_name="store_access_audit_events")
    op.drop_table("store_access_audit_events")

    op.drop_index(op.f("ix_store_access_grants_viewer_user_id"), table_name="store_access_grants")
    op.drop_index(op.f("ix_store_access_grants_store_owner_user_id"), table_name="store_access_grants")
    op.drop_table("store_access_grants")

