"""Обработчики команд. Каждый возвращает код выхода; разбор аргументов в cli.py."""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from pathlib import Path

from wbf_support_bot.config import Config, ConfigError, describe_config, load_config
from wbf_support_bot.fmt import fmt_ts, format_history, format_message_detail
from wbf_support_bot.poller import Poller, run_loop
from wbf_support_bot.redaction import register_secret
from wbf_support_bot.send import SendError, send_message
from wbf_support_bot.store import Store
from wbf_support_bot.telegram import TelegramClient
from wbf_support_bot.transcribe import OpenAITranscriber
from wbf_support_bot.watch import WatchSettings, run_watch

EXIT_REJECTED = 1
EXIT_CONFIG = 2

logger = logging.getLogger("wbf_support_bot")


def open_config(config_path: Path) -> Config:
    """Загружает конфиг и регистрирует секреты, чтобы их маскировали все последующие сообщения."""
    config = load_config(config_path)
    for secret in config.secrets():
        register_secret(secret)
    return config


def open_runtime(config_path: Path) -> tuple[Config, Store]:
    config = open_config(config_path)
    return config, Store(config.db_path)


def cmd_run(config_path: Path) -> int:
    config, store = open_runtime(config_path)
    telegram = TelegramClient(config.bot_token)
    transcriber = (
        OpenAITranscriber(config.openai_api_key, config.transcribe_model)
        if config.openai_api_key
        else None
    )
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("получен сигнал %s: остановка после текущего опроса", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        poller = Poller(config=config, store=store, api=telegram, transcriber=transcriber)
        logger.info(
            "бот запущен; база: %s; клиентских чатов: %d; расшифровка голосовых: %s",
            config.db_path,
            len(config.chats),
            "да" if transcriber else "нет",
        )
        run_loop(store=store, poller=poller, stop=stop)
        logger.info("бот остановлен")
        return 0
    finally:
        telegram.close()
        store.close()


def cmd_check_config(config_path: Path) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print("Конфигурация не готова. Исправьте перечисленное и запустите проверку снова:")
        for problem in exc.problems:
            print(f"  - {problem}")
        return EXIT_CONFIG
    print("Конфигурация в порядке.")
    print(f"Файл: {config_path}")
    for line in describe_config(config):
        print(line)
    mode = config_path.stat().st_mode & 0o777
    if mode & 0o077:
        print(
            f"ВНИМАНИЕ: файл доступен другим пользователям системы (права {oct(mode)}). "
            f"Выполните: chmod 600 {config_path}"
        )
    return 0


def cmd_check(config_path: Path) -> int:
    config = open_config(config_path)
    telegram = TelegramClient(config.bot_token)
    try:
        me = telegram.get_me()
    finally:
        telegram.close()
    print(
        f"Бот отвечает: @{me.get('username', '?')} (имя «{me.get('first_name', '?')}», "
        f"id {me.get('id', '?')})"
    )
    return 0


def cmd_watch(config_path: Path, *, quiet_sec: float, max_wait_sec: float) -> int:
    config, store = open_runtime(config_path)
    try:
        return run_watch(
            config=config,
            store=store,
            settings=WatchSettings(quiet_sec=quiet_sec, max_wait_sec=max_wait_sec),
        )
    finally:
        store.close()


def cmd_send(
    config_path: Path,
    *,
    chat_id: int,
    key: str,
    text: str,
    reply_to: int | None,
    file_path: Path | None,
) -> int:
    config, store = open_runtime(config_path)
    telegram = TelegramClient(config.bot_token)
    try:
        body = sys.stdin.read() if text == "-" else text
        outcome = send_message(
            store=store,
            api=telegram,
            config=config,
            chat_id=chat_id,
            key=key,
            text=body,
            reply_to=reply_to,
            file_path=file_path,
        )
    except SendError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False, indent=2))
        return EXIT_REJECTED
    finally:
        telegram.close()
        store.close()
    print(json.dumps(outcome.as_dict(config.chats), ensure_ascii=False, indent=2))
    if outcome.warning:
        print(f"ВНИМАНИЕ: {outcome.warning}", file=sys.stderr)
    return outcome.exit_code


def cmd_history(config_path: Path, *, chat_id: int, limit: int) -> int:
    config, store = open_runtime(config_path)
    try:
        print(
            format_history(
                chat_id=chat_id,
                chat_name=config.chats.get(chat_id),
                messages=store.chat_messages(chat_id, limit),
                outbox=store.outbox_for_chat(chat_id, limit),
                staff_ids=config.staff_ids,
            )
        )
    finally:
        store.close()
    return 0


def cmd_show(config_path: Path, *, row_id: int) -> int:
    config, store = open_runtime(config_path)
    try:
        row = store.get_message(row_id)
    finally:
        store.close()
    if row is None:
        print(f"сообщения с номером {row_id} в базе нет", file=sys.stderr)
        return EXIT_REJECTED
    print(format_message_detail(row, config.chats))
    return 0


def cmd_download(config_path: Path, *, row_id: int, out: Path) -> int:
    config, store = open_runtime(config_path)
    try:
        row = store.get_message(row_id)
    finally:
        store.close()
    if row is None or not row.file_id:
        print(f"у записи {row_id} нет вложения", file=sys.stderr)
        return EXIT_REJECTED
    telegram = TelegramClient(config.bot_token)
    try:
        info = telegram.get_file(row.file_id)
        data = telegram.download_file(str(info["file_path"]))
    finally:
        telegram.close()
    target = out.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(json.dumps({"saved": str(target), "bytes": len(data)}, ensure_ascii=False))
    return 0


def cmd_chats(config_path: Path) -> int:
    config, store = open_runtime(config_path)
    try:
        seen = store.list_chats()
    finally:
        store.close()
    seen_ids = {chat.chat_id for chat in seen}
    if seen:
        print("Группы, в которых бот уже видел сообщения:")
    for chat in seen:
        if chat.chat_id in config.chats:
            mark = f"в конфиге как «{config.chats[chat.chat_id]}»"
        else:
            mark = "НЕ в конфиге: сообщения сохраняются, но в watch не попадают"
        print(
            f"  {chat.chat_id}  «{chat.title or 'без названия'}»  — {mark}; "
            f"последнее сообщение {fmt_ts(chat.last_seen)} МСК"
        )
    for chat_id, name in sorted(config.chats.items()):
        if chat_id not in seen_ids:
            print(f"  {chat_id}  (в конфиге: «{name}») — сообщений пока не было")
    if not seen and not config.chats:
        print("Пока ничего не видели. Добавьте бота в группу клиента и напишите там сообщение.")
    return 0
