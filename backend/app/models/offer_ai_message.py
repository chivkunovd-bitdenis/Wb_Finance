from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class OfferAiMessage(Base):
    __tablename__ = "offer_ai_messages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    chat_id = Column(
        UUID(as_uuid=False),
        ForeignKey("offer_ai_chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)  # user|assistant
    content = Column(Text, nullable=False)
    # Snapshot of retrieval context for assistant answers: offer RAG chunks and/or web
    # search citations (unified assistant chat reuses this field for both).
    retrieved_chunks = Column(JSONB, nullable=True)
    # For pattern-B: the standalone question used for retrieval.
    standalone_question = Column(Text, nullable=True)
    # 'chat' (regular Q&A) | 'cfo' (AI CFO daily-brief-style analysis run).
    kind = Column(String(16), nullable=False, default="chat")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant')", name="ck_offer_ai_message_role"),
        CheckConstraint("kind in ('chat', 'cfo')", name="ck_offer_ai_message_kind"),
    )

