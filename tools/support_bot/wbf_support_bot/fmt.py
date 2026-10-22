"""Вывод для человека и для AI-сессии: время по Москве, тексты сообщений, блоки watch и history."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone

from wbf_support_bot.messages import VOICE_NO_KEY
from wbf_support_bot.store import MessageRow, OutboxRow

# Москва с 2014 года живёт на UTC+3 без перехода на летнее время.
MSK = timezone(timedelta(hours=3), "МСК")
_SNIPPET_LIMIT = 300


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, MSK).strftime("%d.%m.%Y %H:%M")


def body_text(row: MessageRow) -> str:
    """Текст сообщения для показа: текст, расшифровка, подпись или понятная заглушка."""
    if row.kind == "text":
        return row.text or "(пустое сообщение)"
    if row.kind == "voice":
        return row.text or VOICE_NO_KEY
    if row.kind == "photo":
        return f"(фото) {row.caption}" if row.caption else "(фото без подписи)"
    if row.kind == "document":
        name = row.file_name or "без имени"
        return f"(файл: {name}) {row.caption}" if row.caption else f"(файл: {name})"
    if row.caption:
        return f"(сообщение другого типа) {row.caption}"
    return "(сообщение другого типа: стикер, видео, опрос и т.п.)"


def _snippet(text: str | None) -> str:
    if not text:
        return "(без текста)"
    if len(text) > _SNIPPET_LIMIT:
        return text[:_SNIPPET_LIMIT] + "…"
    return text


def _header_lines(row: MessageRow, chat_names: Mapping[int, str]) -> list[str]:
    client = chat_names.get(row.chat_id, "(чат не в конфиге: сообщение не попадает в watch)")
    author = row.author_name or "(имя не указано)"
    lines = [
        f"Клиент: {client}",
        f"Чат: {row.chat_id}, номер в базе: {row.id}, id сообщения в Telegram: {row.msg_id}",
        f"Автор: {author} (id {row.author_id})",
        f"Время: {fmt_ts(row.ts)} МСК, тип: {row.kind}",
    ]
    if row.reply_to_msg_id is not None:
        snippet = _snippet(row.reply_to_text)
        lines.append(f"Это ответ на сообщение {row.reply_to_msg_id}: «{snippet}»")
    return lines


def format_client_batch(rows: Sequence[MessageRow], chat_names: Mapping[int, str]) -> str:
    lines = [f"=== НОВЫЕ СООБЩЕНИЯ КЛИЕНТОВ: {len(rows)} ==="]
    for index, row in enumerate(rows, start=1):
        lines.append("")
        lines.append(f"--- сообщение {index} из {len(rows)} ---")
        lines.extend(_header_lines(row, chat_names))
        lines.append(f"Ответить: --chat {row.chat_id} --reply-to {row.msg_id}")
        lines.append("Текст:")
        lines.append(body_text(row))
    return "\n".join(lines)


def format_message_detail(row: MessageRow, chat_names: Mapping[int, str]) -> str:
    lines = _header_lines(row, chat_names)
    lines.append(f"Статус: {row.status}")
    if row.kind == "voice":
        lines.append(f"Попыток расшифровки: {row.attempts}")
    if row.file_id:
        lines.append(f"Вложение: есть (file_id сохранён, имя: {row.file_name or 'нет'})")
    if row.edited_ts is not None:
        lines.append(f"Отредактировано: {fmt_ts(row.edited_ts)} МСК")
    lines.append("Текст:")
    lines.append(body_text(row))
    return "\n".join(lines)


def format_history(
    *,
    chat_id: int,
    chat_name: str | None,
    messages: Sequence[MessageRow],
    outbox: Sequence[OutboxRow],
    staff_ids: Iterable[int],
) -> str:
    staff = set(staff_ids)
    header = chat_name if chat_name else "(чат не в конфиге)"
    entries: list[tuple[int, str]] = []
    for row in messages:
        if row.author_id in staff:
            who = f"сотрудник {row.author_name or row.author_id}"
        else:
            who = f"клиент {row.author_name or '(имя не указано)'}"
        entries.append(
            (
                row.ts,
                f"[{fmt_ts(row.ts)}] {who} (база {row.id}, msg {row.msg_id}): {body_text(row)}",
            )
        )
    for item in outbox:
        stamp = item.sent_at or item.created_at
        note = "" if item.status == "sent" else f" (статус: {item.status})"
        entries.append((stamp, f"[{fmt_ts(stamp)}] мы (бот){note}: {item.text}"))
    entries.sort(key=lambda entry: entry[0])
    lines = [f"=== История: {header}, чат {chat_id} ==="]
    lines.extend(text for _, text in entries)
    if not entries:
        lines.append("(записей нет)")
    return "\n".join(lines)
