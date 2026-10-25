"""Разбор входящего сообщения Telegram (словарь из Bot API) в запись для базы."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VOICE_PREFIX = "(расшифровка голосового) "
VOICE_NO_KEY = "(голосовое сообщение, без расшифровки)"
VOICE_PENDING = "(голосовое сообщение, расшифровывается)"
VOICE_FAILED = "(голосовое сообщение, расшифровать не удалось)"
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})


@dataclass(frozen=True)
class MessageRecord:
    chat_id: int
    chat_title: str
    msg_id: int
    author_id: int | None
    author_name: str
    ts: int
    kind: str
    text: str | None
    caption: str | None
    file_id: str | None
    file_name: str | None
    reply_to_msg_id: int | None
    reply_to_text: str | None
    status: str
    edited_ts: int | None = None


def display_name(user: dict[str, Any] | None) -> str:
    if not user:
        return ""
    full = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    username = user.get("username")
    if username:
        return f"{full} (@{username})" if full else f"@{username}"
    return full


def chat_title(chat: dict[str, Any]) -> str:
    if chat.get("title"):
        return str(chat["title"])
    personal = " ".join(part for part in (chat.get("first_name"), chat.get("last_name")) if part)
    return personal or str(chat.get("username") or chat.get("id", ""))


def parse_message(
    msg: dict[str, Any], *, transcribe_voice: bool, edited: bool = False
) -> MessageRecord:
    """Превращает сообщение Telegram в запись. Голосовые сразу получают статус расшифровки."""
    chat = msg["chat"]
    sender = msg.get("from")
    author_id = int(sender["id"]) if isinstance(sender, dict) and "id" in sender else None

    reply = msg.get("reply_to_message")
    reply_to_msg_id: int | None = None
    reply_to_text: str | None = None
    if isinstance(reply, dict):
        reply_to_msg_id = int(reply["message_id"])
        reply_to_text = str(reply.get("text") or reply.get("caption") or "")

    kind = "other"
    text: str | None = None
    caption: str | None = msg.get("caption")
    file_id: str | None = None
    file_name: str | None = None
    status = "ok"

    if "voice" in msg:
        kind = "voice"
        file_id = msg["voice"].get("file_id")
        if transcribe_voice and file_id:
            status = "transcribing"
            text = VOICE_PENDING
        else:
            text = VOICE_NO_KEY
    elif msg.get("photo"):
        kind = "photo"
        file_id = msg["photo"][-1].get("file_id")
    elif "document" in msg:
        kind = "document"
        file_id = msg["document"].get("file_id")
        file_name = msg["document"].get("file_name")
    elif "text" in msg:
        kind = "text"
        text = str(msg["text"])
        caption = None

    edit_date = msg.get("edit_date") if edited else None
    return MessageRecord(
        chat_id=int(chat["id"]),
        chat_title=chat_title(chat),
        msg_id=int(msg["message_id"]),
        author_id=author_id,
        author_name=display_name(sender if isinstance(sender, dict) else None),
        ts=int(msg["date"]),
        kind=kind,
        text=text,
        caption=caption,
        file_id=file_id,
        file_name=file_name,
        reply_to_msg_id=reply_to_msg_id,
        reply_to_text=reply_to_text,
        status=status,
        edited_ts=int(edit_date) if edit_date is not None else None,
    )
