from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.offer_ai_chat import OfferAiChat
from app.models.offer_ai_message import OfferAiMessage
from app.models.user import User
from app.services.offer_rag_service import ask_offer

logger = logging.getLogger(__name__)


MAX_FIELD_BYTES = 50_000
HISTORY_LIMIT = 10  # 10 реплик user+assistant суммарно


def _clip_bytes(s: str, *, limit: int = MAX_FIELD_BYTES) -> str:
    raw = (s or "").encode("utf-8")
    if len(raw) <= limit:
        return s or ""
    return raw[:limit].decode("utf-8", errors="ignore")


def require_admin(user: User) -> None:
    if not bool(getattr(user, "is_admin", False)):
        # скрываем функционал не-админу
        raise PermissionError("Требуются права администратора")


def get_or_create_chat(*, db: Session, chat_id: str, user: User, offer_version: str) -> OfferAiChat:
    chat = (
        db.query(OfferAiChat)
        .filter(OfferAiChat.id == chat_id)
        .filter(OfferAiChat.user_id == user.id)
        .first()
    )
    if chat:
        if chat.offer_version != offer_version:
            raise ValueError("Чат относится к другой версии оферты. Создайте новый чат.")
        return chat
    chat = OfferAiChat(id=chat_id, user_id=user.id, offer_version=offer_version)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def reset_chat(*, db: Session, chat_id: str, user: User) -> None:
    chat = (
        db.query(OfferAiChat)
        .filter(OfferAiChat.id == chat_id)
        .filter(OfferAiChat.user_id == user.id)
        .first()
    )
    if not chat:
        return
    db.query(OfferAiMessage).filter(OfferAiMessage.chat_id == chat.id).delete()
    db.query(OfferAiChat).filter(OfferAiChat.id == chat.id).delete()
    db.commit()


def load_history(*, db: Session, chat_id: str, user: User, offer_version: str) -> list[OfferAiMessage]:
    chat = (
        db.query(OfferAiChat)
        .filter(OfferAiChat.id == chat_id)
        .filter(OfferAiChat.user_id == user.id)
        .first()
    )
    if not chat:
        return []
    if chat.offer_version != offer_version:
        raise ValueError("Чат относится к другой версии оферты. Создайте новый чат.")
    msgs = (
        db.query(OfferAiMessage)
        .filter(OfferAiMessage.chat_id == chat.id)
        .order_by(OfferAiMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    return list(reversed(msgs))


@dataclass(frozen=True)
class CondenseResult:
    standalone_question: str
    need_clarification: bool
    clarifying_question: str | None


def condense_question(*, history: list[OfferAiMessage], message: str) -> CondenseResult:
    """
    Patten-B: сначала сделать самостоятельный вопрос из истории + нового сообщения.
    Если смысл нельзя восстановить без уточнения — вернуть уточняющий вопрос.
    """
    from llama_index.core import Settings

    # компактная история
    hist_lines: list[str] = []
    for m in history[-HISTORY_LIMIT:]:
        role = "Пользователь" if m.role == "user" else "Ассистент"
        hist_lines.append(f"{role}: {m.content}")
    hist = "\n".join(hist_lines).strip()

    prompt = f"""Ты — помощник, который переформулирует вопрос пользователя в самостоятельный.
Важно: нельзя добавлять факты, которых нет в истории. Если вопрос неясен (\"это\", \"так\", \"там\") и из истории нельзя понять, что имеется в виду — верни need_clarification=true и задай один уточняющий вопрос.
Ответ строго в JSON без пояснений, поля:
- standalone_question: string
- need_clarification: boolean
- clarifying_question: string|null

История диалога:
{hist or "—"}

Новое сообщение пользователя:
{message}
"""

    raw = Settings.llm.complete(prompt).text  # type: ignore[no-any-return]
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
    except Exception:
        # fallback: считаем, что уточнение не нужно
        return CondenseResult(standalone_question=message, need_clarification=False, clarifying_question=None)
    standalone = str(data.get("standalone_question") or "").strip()
    need = bool(data.get("need_clarification"))
    clar = data.get("clarifying_question")
    clar_s = str(clar).strip() if clar is not None else None
    if need and clar_s:
        return CondenseResult(standalone_question=standalone or message, need_clarification=True, clarifying_question=clar_s)
    if not standalone:
        standalone = message
    return CondenseResult(standalone_question=standalone, need_clarification=False, clarifying_question=None)


def chat_ask(
    *,
    db: Session,
    user: User,
    chat_id: str,
    offer_version: str,
    message: str,
) -> dict[str, Any]:
    message = _clip_bytes(message)
    history = load_history(db=db, chat_id=chat_id, user=user, offer_version=offer_version)
    cond = condense_question(history=history, message=message)

    # сохраняем user message
    db.add(OfferAiMessage(chat_id=chat_id, role="user", content=message))
    db.commit()

    if cond.need_clarification and cond.clarifying_question:
        answer = _clip_bytes(cond.clarifying_question)
        db.add(
            OfferAiMessage(
                chat_id=chat_id,
                role="assistant",
                content=answer,
                standalone_question=cond.standalone_question,
                retrieved_chunks=None,
            )
        )
        db.commit()
        return {
            "answer": answer,
            "sources": [],
            "standalone_question": cond.standalone_question,
            "need_clarification": True,
        }

    answer, sources = ask_offer(question=cond.standalone_question, active_version=offer_version)
    # snapshot 5 chunks + meta
    snap = []
    for s in sources[:5]:
        snap.append(
            {
                "chunk_id": s.chunk_id,
                "score": s.score,
                "text": _clip_bytes(s.text),
                "metadata": getattr(s, "metadata", None),
            }
        )

    answer = _clip_bytes(answer)
    db.add(
        OfferAiMessage(
            chat_id=chat_id,
            role="assistant",
            content=answer,
            standalone_question=cond.standalone_question,
            retrieved_chunks=snap,
        )
    )
    # last_activity
    db.query(OfferAiChat).filter(OfferAiChat.id == chat_id).update({"last_activity_at": datetime.now(UTC)})
    db.commit()
    return {
        "answer": answer,
        "sources": sources,
        "standalone_question": cond.standalone_question,
        "need_clarification": False,
    }

