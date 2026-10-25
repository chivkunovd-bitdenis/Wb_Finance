"""Команда watch: ждёт новых сообщений клиентов и завершается, чтобы разбудить AI-сессию.

Команда молчит, пока ничего не произошло. При событии она печатает текст и завершается:
код 0 для сообщений, тишины и напоминания об обещании, код 1 если бот не работает.
Завершение процесса и есть сигнал «пора читать». После ответа сессия запускает watch снова.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from wbf_support_bot.config import Config
from wbf_support_bot.fmt import fmt_ts, format_client_batch
from wbf_support_bot.store import OutboxRow, Store

EXIT_OK = 0
EXIT_BOT_DOWN = 1
WATERMARK_FILE = "watch.last"
PROMISE_FILE = "promise_alerts.json"
PROMISE_MARKERS = ("разбираемся", "проверим", "напишу", "вернусь", "уточню")


class WatchStateError(RuntimeError):
    """Служебный файл watch повреждён. Текст подсказывает, что делать."""


@dataclass(frozen=True)
class WatchSettings:
    quiet_sec: float = 45
    max_wait_sec: float = 7200
    poll_sec: float = 20
    heartbeat_max_age_sec: float = 180
    promise_age_sec: float = 3600


def read_watermark(state_dir: Path) -> int | None:
    path = state_dir / WATERMARK_FILE
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        raise WatchStateError(
            f"файл {path} повреждён: удалите его, и watch начнёт с текущего момента"
        ) from None


def write_watermark(state_dir: Path, value: int) -> None:
    _atomic_write(state_dir / WATERMARK_FILE, str(value))


def has_promise(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in PROMISE_MARKERS)


def find_overdue_promise(
    store: Store,
    *,
    chat_ids: Iterable[int],
    now: float,
    min_age_sec: float,
    alerted: set[int],
) -> OutboxRow | None:
    """Ищет наше последнее отправленное сообщение в клиентском чате с обещанием без ответа.

    Условия: сообщение в клиентском чате, в тексте есть маркер обещания, прошло не
    меньше min_age_sec, и после него мы ничего не отправляли (это последнее отправленное
    сообщение в чате). Каждое сообщение тревожит один раз, см. promise_alerts.json.
    """
    client_chats = set(chat_ids)
    for row in store.last_sent_per_chat():
        if row.chat_id not in client_chats or row.id in alerted:
            continue
        if not has_promise(row.text):
            continue
        sent_at = row.sent_at or row.created_at
        if now - sent_at >= min_age_sec:
            return row
    return None


def run_watch(
    *,
    config: Config,
    store: Store,
    settings: WatchSettings,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    chat_ids = sorted(config.chats)
    staff = sorted(config.staff_ids)
    watermark = read_watermark(config.state_dir)
    if watermark is None:
        # Первый запуск: всё, что уже есть в базе, считаем прочитанным. Ждём только новое.
        watermark = store.max_message_id()
        write_watermark(config.state_dir, watermark)

    started = clock()
    quiet_started: float | None = None
    while True:
        now = clock()
        age = _heartbeat_age(store, now)
        if age is None or age > settings.heartbeat_max_age_sec:
            out(_bot_down_text(age))
            return EXIT_BOT_DOWN

        alerted = _load_alerted(config.state_dir)
        overdue = find_overdue_promise(
            store,
            chat_ids=chat_ids,
            now=now,
            min_age_sec=settings.promise_age_sec,
            alerted=alerted,
        )
        if overdue is not None:
            out(_promise_text(overdue, config, now))
            _save_alerted(config.state_dir, alerted | {overdue.id})
            return EXIT_OK

        pending = store.client_messages_after(watermark, chat_ids, staff)
        if pending:
            if any(row.status == "transcribing" for row in pending):
                # Ждём расшифровку голосового, иначе отдадим сессии пустой текст.
                quiet_started = None
            else:
                if quiet_started is None:
                    quiet_started = now
                if now - quiet_started >= settings.quiet_sec:
                    batch = store.client_messages_after(watermark, chat_ids, staff)
                    if batch and all(row.status != "transcribing" for row in batch):
                        out(format_client_batch(batch, config.chats))
                        write_watermark(config.state_dir, batch[-1].id)
                        return EXIT_OK
        elif now - started >= settings.max_wait_sec:
            out(f"тишина: за {settings.max_wait_sec:.0f} с новых сообщений клиентов не было")
            return EXIT_OK

        sleep(settings.poll_sec)


def _heartbeat_age(store: Store, now: float) -> float | None:
    raw = store.get_kv("heartbeat")
    if raw is None:
        return None
    try:
        return now - float(raw)
    except ValueError:
        return None


def _bot_down_text(age: float | None) -> str:
    if age is None:
        return (
            "БОТ НЕ ЗАПУЩЕН: признаков работы бота нет (он не запускался или база пуста). "
            "Проверьте сервис pro.wbfinance.support-bot и файл config.json."
        )
    minutes = int(age // 60)
    return (
        f"БОТ НЕ ЗАПУЩЕН: последний признак жизни был {minutes} мин назад (порог 3 мин). "
        "Новые сообщения клиентов сейчас могут не записываться. "
        "Перезапустите сервис и посмотрите журнал ~/Library/Logs/wbf-support-bot.log."
    )


def _promise_text(row: OutboxRow, config: Config, now: float) -> str:
    client = config.chats.get(row.chat_id, "(чат не в конфиге)")
    sent_at = row.sent_at or row.created_at
    minutes = int((now - sent_at) // 60)
    return (
        f"ОБЕЩАНИЕ БЕЗ ОТВЕТА: в чате «{client}» мы написали {fmt_ts(sent_at)} МСК "
        f"({minutes} мин назад) и пообещали вернуться, но после этого ответа не было.\n"
        f"Наш текст:\n{row.text}\n"
        "Нужно либо ответить клиенту по существу, либо коротко написать, на каком этапе вопрос."
    )


def _load_alerted(state_dir: Path) -> set[int]:
    path = state_dir / PROMISE_FILE
    if not path.exists():
        return set()
    try:
        return {int(item) for item in json.loads(path.read_text(encoding="utf-8"))}
    except (ValueError, TypeError):
        raise WatchStateError(
            f"файл {path} повреждён: удалите его, и напоминания о обещаниях начнутся заново"
        ) from None


def _save_alerted(state_dir: Path, ids: set[int]) -> None:
    _atomic_write(state_dir / PROMISE_FILE, json.dumps(sorted(ids)))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
