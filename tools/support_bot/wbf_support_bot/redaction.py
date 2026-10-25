"""Маскировка секретов (токен бота, ключ OpenAI) в логах и на экране.

Секреты регистрируются один раз, когда загружена конфигурация. Всё, что уходит
в лог или в текст ошибки, проходит через redact(). Даже если исключение
библиотеки случайно покажет адрес запроса, токен в выводе будет замаскирован.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

MASK = "***"
_registry: set[str] = set()


def register_secret(value: str) -> None:
    if value:
        _registry.add(value)


def redact(text: str, secrets: Iterable[str] | None = None) -> str:
    values = _registry if secrets is None else {value for value in secrets if value}
    result = text
    for secret in sorted(values, key=len, reverse=True):
        result = result.replace(secret, MASK)
    return result


class RedactingFormatter(logging.Formatter):
    """Форматтер, который маскирует секреты во всей строке лога, включая traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
