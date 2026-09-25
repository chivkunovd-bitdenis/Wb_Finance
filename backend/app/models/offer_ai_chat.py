from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class OfferAiChat(Base):
    __tablename__ = "offer_ai_chats"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Store this chat's history belongs to (may differ from user_id when viewing a granted store).
    # Nullable + backfilled for pre-existing offer-admin chats (see migration a3f7c9e1d2b4).
    store_owner_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Nullable: only the legacy offer-admin chat is scoped to one offer version.
    offer_version = Column(String(64), nullable=True, index=True)
    # 'offer' (legacy admin offer-only chat) | 'assistant' (unified ИИ-чат: RAG + web + store data).
    kind = Column(String(16), nullable=False, default="offer")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

