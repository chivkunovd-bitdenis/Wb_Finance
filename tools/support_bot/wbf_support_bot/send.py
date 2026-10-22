"""Команда send: отправка сообщения клиенту через бота, защищённая от повторной отправки.

Правила идемпотентности (ключ --key уникален для каждого ответа):
- записи с ключом нет: создаём строку pending, фиксируем, переводим в sending и только
  потом обращаемся к Telegram;
- статус sent: ничего не отправляем, просто показываем результат;
- статус sending или unknown: ничего не отправляем, предупреждаем, потому что вызов
  Telegram мог уйти и сообщение уже у клиента (например, при обрыве сети);
- статус failed или pending: Telegram точно не принял сообщение, поэтому отправка разрешена.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from wbf_support_bot.config import Config
from wbf_support_bot.store import OutboxRow, Store
from wbf_support_bot.telegram import TelegramError
from wbf_support_bot.textsplit import TELEGRAM_LIMIT, split_text, utf16_len

CAPTION_LIMIT = 1024
EXIT_SENT = 0
EXIT_FAILED = 1
EXIT_UNSURE = 3


class SendError(Exception):
    """Запрос отклонён до обращения к Telegram: нельзя писать в этот чат, пустой текст и т.п."""


class SendAPI(Protocol):
    def send_message(
        self, *, chat_id: int, text: str, reply_to: int | None = None
    ) -> dict[str, Any]: ...

    def send_document(
        self, *, chat_id: int, path: Path, caption: str | None, reply_to: int | None
    ) -> dict[str, Any]: ...


@dataclass
class SendOutcome:
    key: str
    chat_id: int
    status: str
    tg_message_ids: list[int] = field(default_factory=list)
    already_sent: bool = False
    text_matches: bool | None = None
    warning: str | None = None
    error: str | None = None

    @property
    def exit_code(self) -> int:
        if self.status == "sent":
            return EXIT_SENT
        if self.status in ("sending", "unknown"):
            return EXIT_UNSURE
        return EXIT_FAILED

    def as_dict(self, chat_names: Mapping[int, str]) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "chat_id": self.chat_id,
            "chat": chat_names.get(self.chat_id, "личный чат владельца"),
            "status": self.status,
            "tg_message_ids": self.tg_message_ids,
            "already_sent": self.already_sent,
        }
        if self.text_matches is not None:
            data["text_matches"] = self.text_matches
        if self.warning:
            data["warning"] = self.warning
        if self.error:
            data["error"] = self.error
        return data


def plan_steps(text: str, file_path: Path | None) -> list[tuple[str, str | None]]:
    """Список отправок: ('message', текст) или ('document', подпись или None)."""
    if file_path is None:
        return [("message", part) for part in split_text(text, TELEGRAM_LIMIT)]
    stripped = text.strip()
    if stripped and utf16_len(stripped) <= CAPTION_LIMIT:
        return [("document", stripped)]
    steps: list[tuple[str, str | None]] = [
        ("message", part) for part in split_text(stripped, TELEGRAM_LIMIT)
    ]
    steps.append(("document", None))
    return steps


def send_message(
    *,
    store: Store,
    api: SendAPI,
    config: Config,
    chat_id: int,
    key: str,
    text: str,
    reply_to: int | None = None,
    file_path: Path | None = None,
    now: int | None = None,
) -> SendOutcome:
    if not key.strip():
        raise SendError("ключ --key пустой: нужен уникальный ключ ответа")
    if not config.can_send_to(chat_id):
        raise SendError(f"чат {chat_id} не в конфиге и не личный чат владельца: отправка запрещена")
    if not text.strip() and file_path is None:
        raise SendError("текст пустой")
    if file_path is not None and not file_path.is_file():
        raise SendError(f"файл не найден: {file_path}")

    stamp = int(time.time()) if now is None else now
    existing = store.get_outbox(key)
    if existing is None:
        store.insert_outbox_pending(
            key=key,
            chat_id=chat_id,
            text=text,
            reply_to_msg_id=reply_to,
            file_path=str(file_path) if file_path else None,
            created_at=stamp,
        )
        existing = store.get_outbox(key)
    if existing is None:  # pragma: no cover - защита от гонки, на практике не случается
        raise SendError("не удалось создать запись в журнале отправок")

    if existing.status not in ("pending", "failed") or not store.claim_outbox(key):
        return _outcome_for_existing(store.get_outbox(key) or existing, text, file_path)

    return _deliver(
        store=store,
        api=api,
        key=key,
        chat_id=chat_id,
        text=text,
        reply_to=reply_to,
        file_path=file_path,
        stamp=stamp,
    )


def _outcome_for_existing(row: OutboxRow, text: str, file_path: Path | None) -> SendOutcome:
    matches = row.text == text and row.file_path == (str(file_path) if file_path else None)
    outcome = SendOutcome(
        key=row.key,
        chat_id=row.chat_id,
        status=row.status,
        tg_message_ids=row.tg_ids(),
        text_matches=matches,
    )
    if row.status == "sent":
        outcome.already_sent = True
        return outcome
    if row.status in ("sending", "unknown"):
        outcome.warning = (
            "предыдущая отправка с этим ключом не завершилась понятно (статус "
            f"{row.status}). Повторно не отправляю: сообщение могло уйти. "
            "Проверьте чат вручную, а при необходимости используйте новый ключ."
        )
        return outcome
    outcome.warning = "запись с этим ключом занята другим процессом; повторно не отправляю"
    return outcome


def _deliver(
    *,
    store: Store,
    api: SendAPI,
    key: str,
    chat_id: int,
    text: str,
    reply_to: int | None,
    file_path: Path | None,
    stamp: int,
) -> SendOutcome:
    sent_ids: list[int] = []
    try:
        for index, (kind, payload) in enumerate(plan_steps(text, file_path)):
            step_reply = reply_to if index == 0 else None
            if kind == "document" and file_path is not None:
                result = api.send_document(
                    chat_id=chat_id, path=file_path, caption=payload, reply_to=step_reply
                )
            else:
                result = api.send_message(chat_id=chat_id, text=payload or "", reply_to=step_reply)
            sent_ids.append(int(result["message_id"]))
    except TelegramError as exc:
        if not sent_ids and exc.code is not None and 400 <= exc.code < 500:
            # Telegram ясно ответил отказом: сообщение не ушло, повтор с этим ключом разрешён.
            store.finish_outbox(key, status="failed", error=str(exc))
            return SendOutcome(key=key, chat_id=chat_id, status="failed", error=str(exc))
        return _mark_unknown(store, key, chat_id, sent_ids, str(exc))
    except Exception as exc:
        # Сеть оборвалась уже после отправки или произошло что-то непредвиденное:
        # не знаем, дошло ли сообщение, поэтому повтор запрещаем.
        return _mark_unknown(store, key, chat_id, sent_ids, type(exc).__name__)

    store.finish_outbox(key, status="sent", tg_message_ids=sent_ids, sent_at=stamp)
    return SendOutcome(
        key=key,
        chat_id=chat_id,
        status="sent",
        tg_message_ids=sent_ids,
    )


def _mark_unknown(
    store: Store, key: str, chat_id: int, sent_ids: list[int], error: str
) -> SendOutcome:
    store.finish_outbox(key, status="unknown", tg_message_ids=sent_ids, error=error)
    return SendOutcome(
        key=key,
        chat_id=chat_id,
        status="unknown",
        tg_message_ids=sent_ids,
        error=error,
    )
