"""Опрос Telegram (getUpdates), сохранение сообщений и цикл команды run.

Порядок в одном опросе важен: сначала сообщения записываются и фиксируются (COMMIT),
и только потом сохраняется смещение (offset). Если процесс упадёт между этими шагами,
Telegram повторно пришлёт те же обновления, а уникальный ключ (чат, id сообщения)
не даст создать дубликат.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from wbf_support_bot.config import Config
from wbf_support_bot.messages import (
    GROUP_CHAT_TYPES,
    VOICE_FAILED,
    VOICE_PENDING,
    VOICE_PREFIX,
    chat_title,
    parse_message,
)
from wbf_support_bot.store import Store
from wbf_support_bot.telegram import LONG_POLL_SEC, TelegramError

logger = logging.getLogger("wbf_support_bot")

ALLOWED_UPDATES = ["message", "edited_message"]
MAX_TRANSCRIBE_ATTEMPTS = 3


class TelegramAPI(Protocol):
    def get_updates(
        self, *, offset: int, timeout: int, allowed_updates: list[str]
    ) -> list[dict[str, Any]]: ...

    def get_file(self, file_id: str) -> dict[str, Any]: ...

    def download_file(self, file_path: str) -> bytes: ...


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, filename: str) -> str: ...


class Pollable(Protocol):
    def poll_once(self) -> int: ...


class Poller:
    def __init__(
        self,
        *,
        config: Config,
        store: Store,
        api: TelegramAPI,
        transcriber: Transcriber | None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.store = store
        self.api = api
        self.transcriber = transcriber
        self._clock = clock

    def poll_once(self, *, timeout: int = LONG_POLL_SEC) -> int:
        """Один цикл: забрать обновления, сохранить, зафиксировать, затем сдвинуть offset."""
        offset = int(self.store.get_kv("offset") or 0)
        updates = self.api.get_updates(
            offset=offset, timeout=timeout, allowed_updates=ALLOWED_UPDATES
        )
        if updates:
            with self.store.transaction():
                for update in updates:
                    self._store_update(update)
            # Смещение двигаем только после COMMIT выше.
            next_offset = max(int(update["update_id"]) for update in updates) + 1
            self.store.set_kv("offset", str(next_offset))
        self.transcribe_pending()
        return len(updates)

    def transcribe_pending(self) -> int:
        """Расшифровывает голосовые со статусом transcribing. Ошибку повторяем до трёх раз."""
        if self.transcriber is None:
            return 0
        done = 0
        for row in self.store.pending_transcriptions():
            try:
                if not row.file_id:
                    raise ValueError("у голосового сообщения нет file_id")
                info = self.api.get_file(row.file_id)
                audio = self.api.download_file(str(info["file_path"]))
                text = self.transcriber.transcribe(audio, filename="voice.ogg")
                self.store.set_transcription(
                    row.id, text=VOICE_PREFIX + text, status="ok", attempts=row.attempts
                )
                done += 1
            except Exception as exc:
                attempts = row.attempts + 1
                if attempts >= MAX_TRANSCRIBE_ATTEMPTS:
                    self.store.set_transcription(
                        row.id, text=VOICE_FAILED, status="transcribe_failed", attempts=attempts
                    )
                else:
                    self.store.set_transcription(
                        row.id,
                        text=row.text or VOICE_PENDING,
                        status="transcribing",
                        attempts=attempts,
                    )
                logger.warning(
                    "расшифровка сообщения %s не удалась (попытка %s из %s): %s",
                    row.id,
                    attempts,
                    MAX_TRANSCRIBE_ATTEMPTS,
                    type(exc).__name__,
                )
        return done

    def _store_update(self, update: dict[str, Any]) -> None:
        edited = "edited_message" in update
        msg = update.get("edited_message") if edited else update.get("message")
        if not isinstance(msg, dict):
            return
        chat = msg["chat"]
        if chat.get("type") in GROUP_CHAT_TYPES:
            self.store.touch_chat(int(chat["id"]), chat_title(chat), int(self._clock()))
        try:
            record = parse_message(
                msg, transcribe_voice=self.transcriber is not None, edited=edited
            )
        except (KeyError, TypeError, ValueError):
            # Непонятный формат не должен навсегда блокировать очередь обновлений.
            logger.warning("обновление %s пропущено: неожиданный формат", update.get("update_id"))
            return
        self.store.save_message(record, edited=edited)


def run_loop(
    *,
    store: Store,
    poller: Pollable,
    stop: threading.Event,
    initial_backoff: float = 1.0,
    backoff_max: float = 60.0,
) -> None:
    """Цикл опроса до сигнала остановки. Пауза растёт при сбоях и не больше backoff_max."""
    delay = initial_backoff
    while not stop.is_set():
        store.set_kv("heartbeat", f"{time.time():.0f}")
        try:
            poller.poll_once()
        except TelegramError as exc:
            if exc.code == 409:
                logger.error(
                    "конфликт: другая копия бота уже опрашивает этот токен (getUpdates 409). "
                    "Остановите лишнюю копию. Повтор через %.0f с.",
                    delay,
                )
            else:
                logger.warning("ошибка Telegram: %s. Повтор через %.0f с.", exc, delay)
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("сеть недоступна (%s). Повтор через %.0f с.", type(exc).__name__, delay)
        except Exception:
            logger.exception("неожиданная ошибка опроса. Повтор через %.0f с.", delay)
        else:
            delay = initial_backoff
            continue
        stop.wait(delay)
        delay = min(delay * 2, backoff_max)
