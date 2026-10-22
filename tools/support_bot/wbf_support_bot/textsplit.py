"""Разбиение длинного текста на части под лимит Telegram (4096 символов).

Telegram считает длину в UTF-16 единицах, поэтому эмодзи, которые занимают
две единицы, учитываются двойным весом. Резать стараемся по абзацам, затем по
строкам, затем по пробелам, и только в крайнем случае посреди слова.
"""

from __future__ import annotations

TELEGRAM_LIMIT = 4096


def utf16_len(text: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    if limit < 1:
        raise ValueError("лимит должен быть положительным")
    text = text.strip()
    if not text:
        return []
    if utf16_len(text) <= limit:
        return [text]
    for separator in ("\n\n", "\n", " "):
        parts = text.split(separator)
        if len(parts) > 1:
            return _pack(parts, separator, limit)
    return _hard_split(text, limit)


def _pack(parts: list[str], separator: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: str | None = None
    for part in parts:
        if utf16_len(part) > limit:
            # Слишком длинный кусок режем дальше, более мелкими разделителями.
            if current is not None:
                chunks.append(current)
                current = None
            chunks.extend(split_text(part, limit))
            continue
        if current is None:
            current = part
        elif utf16_len(current) + utf16_len(separator) + utf16_len(part) <= limit:
            current = current + separator + part
        else:
            chunks.append(current)
            current = part
    if current is not None:
        chunks.append(current)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _hard_split(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    width = 0
    for ch in text:
        step = 2 if ord(ch) > 0xFFFF else 1
        if width + step > limit:
            chunks.append("".join(buffer))
            buffer = []
            width = 0
        buffer.append(ch)
        width += step
    if buffer:
        chunks.append("".join(buffer))
    return chunks
