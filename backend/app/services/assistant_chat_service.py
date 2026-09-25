"""
Персистентность для unified ИИ-чата (/dashboard/assistant/*).

Переиспользует таблицы offer_ai_chats/offer_ai_messages (см. миграцию a3f7c9e1d2b4):
один чат на пару (viewer, store_owner) с kind='assistant', отдельно от старого
admin-only чата про оферту (kind='offer').
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.offer_ai_chat import OfferAiChat
from app.models.offer_ai_message import OfferAiMessage

logger = logging.getLogger(__name__)

MAX_FIELD_BYTES = 50_000
HISTORY_LIMIT = 100
AGENT_CONTEXT_LIMIT = 20


def _clip_bytes(s: str, *, limit: int = MAX_FIELD_BYTES) -> str:
    raw = (s or "").encode("utf-8")
    if len(raw) <= limit:
        return s or ""
    return raw[:limit].decode("utf-8", errors="ignore")


def get_or_create_assistant_chat(*, db: Session, viewer_id: str, store_owner_id: str) -> OfferAiChat:
    chat = (
        db.query(OfferAiChat)
        .filter(
            OfferAiChat.user_id == viewer_id,
            OfferAiChat.store_owner_id == store_owner_id,
            OfferAiChat.kind == "assistant",
        )
        .first()
    )
    if chat:
        return chat
    chat = OfferAiChat(
        user_id=viewer_id,
        store_owner_id=store_owner_id,
        kind="assistant",
        offer_version=None,
    )
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def load_history(*, db: Session, chat: OfferAiChat, limit: int = HISTORY_LIMIT) -> list[OfferAiMessage]:
    msgs = (
        db.query(OfferAiMessage)
        .filter(OfferAiMessage.chat_id == chat.id)
        .order_by(OfferAiMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(msgs))


def append_message(
    *,
    db: Session,
    chat: OfferAiChat,
    role: str,
    content: str,
    kind: str = "chat",
    sources: list[dict[str, Any]] | None = None,
    standalone_question: str | None = None,
) -> OfferAiMessage:
    msg = OfferAiMessage(
        chat_id=chat.id,
        role=role,
        content=_clip_bytes(content),
        kind=kind,
        retrieved_chunks=sources or None,
        standalone_question=standalone_question,
    )
    db.add(msg)
    db.query(OfferAiChat).filter(OfferAiChat.id == chat.id).update({"last_activity_at": datetime.now(UTC)})
    db.commit()
    db.refresh(msg)
    return msg
