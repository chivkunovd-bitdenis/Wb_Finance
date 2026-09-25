"""assistant chat store scope and message kind

Reuses offer_ai_chats/offer_ai_messages for the new unified "ИИ-чат" (AI CFO + offer RAG +
web search agent) instead of a brand-new table pair. Adds:
  - offer_ai_chats.store_owner_id: scopes a chat to (viewer, store) instead of just viewer.
    Nullable + backfilled to user_id for existing (offer-only, admin) chats.
  - offer_ai_chats.offer_version becomes nullable: assistant chats are not tied to a single
    offer version the way the old admin offer-chat was.
  - offer_ai_messages.kind: 'chat' (default, regular Q&A) | 'cfo' (AI CFO analysis run).

Idempotent: every DDL statement checks information_schema first so re-running against a DB
that already has these columns (e.g. partially-applied deploy) is a no-op.

Revision ID: a3f7c9e1d2b4
Revises: f9e0d1c2b3a4
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3f7c9e1d2b4'
down_revision: Union[str, None] = 'f9e0d1c2b3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    chat_cols = {c["name"] for c in inspector.get_columns("offer_ai_chats")}
    message_cols = {c["name"] for c in inspector.get_columns("offer_ai_messages")}

    if "store_owner_id" not in chat_cols:
        op.add_column(
            "offer_ai_chats",
            sa.Column("store_owner_id", sa.UUID(as_uuid=False), nullable=True),
        )
        op.create_foreign_key(
            "fk_offer_ai_chats_store_owner_id_users",
            "offer_ai_chats",
            "users",
            ["store_owner_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(
            op.f("ix_offer_ai_chats_store_owner_id"),
            "offer_ai_chats",
            ["store_owner_id"],
            unique=False,
        )
        # Backfill: existing offer-admin chats are scoped to the viewer's own store.
        op.execute("UPDATE offer_ai_chats SET store_owner_id = user_id WHERE store_owner_id IS NULL")

    # offer_version was NOT NULL; assistant chats aren't scoped to one offer version.
    offer_version_col = next((c for c in inspector.get_columns("offer_ai_chats") if c["name"] == "offer_version"), None)
    if offer_version_col is not None and offer_version_col.get("nullable") is False:
        op.alter_column("offer_ai_chats", "offer_version", existing_type=sa.String(length=64), nullable=True)

    if "kind" not in chat_cols:
        op.add_column(
            "offer_ai_chats",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="offer"),
        )
        op.alter_column("offer_ai_chats", "kind", server_default=None)

    if "kind" not in message_cols:
        op.add_column(
            "offer_ai_messages",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat"),
        )
        op.alter_column("offer_ai_messages", "kind", server_default=None)
        op.create_check_constraint(
            "ck_offer_ai_message_kind",
            "offer_ai_messages",
            "kind in ('chat', 'cfo')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    message_cols = {c["name"] for c in inspector.get_columns("offer_ai_messages")}
    chat_cols = {c["name"] for c in inspector.get_columns("offer_ai_chats")}

    if "kind" in message_cols:
        op.drop_constraint("ck_offer_ai_message_kind", "offer_ai_messages", type_="check")
        op.drop_column("offer_ai_messages", "kind")

    if "kind" in chat_cols:
        op.drop_column("offer_ai_chats", "kind")

    op.alter_column("offer_ai_chats", "offer_version", existing_type=sa.String(length=64), nullable=False)

    if "store_owner_id" in chat_cols:
        op.drop_index(op.f("ix_offer_ai_chats_store_owner_id"), table_name="offer_ai_chats")
        op.drop_constraint("fk_offer_ai_chats_store_owner_id_users", "offer_ai_chats", type_="foreignkey")
        op.drop_column("offer_ai_chats", "store_owner_id")
