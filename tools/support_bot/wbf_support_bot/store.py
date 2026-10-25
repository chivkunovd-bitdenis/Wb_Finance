"""База SQLite (state.db): клиентские сообщения, чаты, исходящие сообщения, служебные значения.

Соединение работает в режиме автокоммита: каждый отдельный запрос сразу фиксируется.
Группу связанных записей оборачивают в transaction(), которая откатывает всё при ошибке.
Файл открыт в режиме WAL, поэтому процесс run и команды send и watch могут работать параллельно.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wbf_support_bot.messages import MessageRecord

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            chat_title TEXT NOT NULL DEFAULT '',
            msg_id INTEGER NOT NULL,
            author_id INTEGER,
            author_name TEXT NOT NULL DEFAULT '',
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('text', 'voice', 'photo', 'document', 'other')),
            text TEXT,
            caption TEXT,
            file_id TEXT,
            file_name TEXT,
            reply_to_msg_id INTEGER,
            reply_to_text TEXT,
            status TEXT NOT NULL DEFAULT 'ok'
                CHECK (status IN ('ok', 'transcribing', 'transcribe_failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            edited_ts INTEGER,
            UNIQUE (chat_id, msg_id)
        )
        """,
        "CREATE INDEX messages_chat_idx ON messages (chat_id, id)",
        """
        CREATE TABLE seen_chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            chat_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            reply_to_msg_id INTEGER,
            file_path TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'sending', 'sent', 'unknown', 'failed')),
            tg_message_id TEXT,
            error TEXT,
            created_at INTEGER NOT NULL,
            sent_at INTEGER
        )
        """,
        "CREATE INDEX outbox_chat_idx ON outbox (chat_id, id)",
    ),
}

_MESSAGE_COLUMNS = (
    "chat_id, chat_title, msg_id, author_id, author_name, ts, kind, text, caption, "
    "file_id, file_name, reply_to_msg_id, reply_to_text, status, edited_ts"
)


class StoreError(Exception):
    """Ошибка базы: например, схема новее, чем умеет эта версия бота."""


@dataclass(frozen=True)
class MessageRow:
    id: int
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
    attempts: int
    edited_ts: int | None


@dataclass(frozen=True)
class OutboxRow:
    id: int
    key: str
    chat_id: int
    text: str
    reply_to_msg_id: int | None
    file_path: str | None
    status: str
    tg_message_id: str | None
    error: str | None
    created_at: int
    sent_at: int | None

    def tg_ids(self) -> list[int]:
        return [int(part) for part in (self.tg_message_id or "").split(",") if part]


@dataclass(frozen=True)
class ChatRow:
    chat_id: int
    title: str
    first_seen: int
    last_seen: int


def _row_to(cls: Any, row: sqlite3.Row) -> Any:
    return cls(**{key: row[key] for key in row.keys()})


class Store:
    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        try:
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            # Откат и при ошибке в теле, и при ошибке самого COMMIT: иначе соединение
            # останется внутри незавершённой транзакции и следующий BEGIN упадёт.
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        with self.transaction():
            current = int(self.get_kv("schema_version") or 0)
            if current > SCHEMA_VERSION:
                raise StoreError(
                    f"база {self.path} создана новой версией бота (схема {current}), "
                    f"а эта версия умеет схему {SCHEMA_VERSION}"
                )
            for version in sorted(MIGRATIONS):
                if version <= current:
                    continue
                for statement in MIGRATIONS[version]:
                    self._conn.execute(statement)
                self.set_kv("schema_version", str(version))

    # служебные значения

    def get_kv(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # сообщения клиентов

    def save_message(self, record: MessageRecord, *, edited: bool) -> None:
        values = (
            record.chat_id,
            record.chat_title,
            record.msg_id,
            record.author_id,
            record.author_name,
            record.ts,
            record.kind,
            record.text,
            record.caption,
            record.file_id,
            record.file_name,
            record.reply_to_msg_id,
            record.reply_to_text,
            record.status,
            record.edited_ts,
        )
        placeholders = ", ".join("?" * 15)
        if edited:
            # Правка: обновляем текст и подпись. Расшифровку голосового не затираем.
            self._conn.execute(
                f"INSERT INTO messages ({_MESSAGE_COLUMNS}) VALUES ({placeholders}) "
                "ON CONFLICT (chat_id, msg_id) DO UPDATE SET "
                "text = CASE WHEN excluded.kind = 'text' "
                "THEN excluded.text ELSE messages.text END, "
                "caption = COALESCE(excluded.caption, messages.caption), "
                "edited_ts = excluded.edited_ts",
                values,
            )
        else:
            # Повторная доставка того же обновления (после сбоя) не создаёт дубликат.
            self._conn.execute(
                f"INSERT OR IGNORE INTO messages ({_MESSAGE_COLUMNS}) VALUES ({placeholders})",
                values,
            )

    def touch_chat(self, chat_id: int, title: str, ts: int) -> None:
        self._conn.execute(
            "INSERT INTO seen_chats (chat_id, title, first_seen, last_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (chat_id) DO UPDATE SET "
            "title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE seen_chats.title END, "
            "last_seen = excluded.last_seen",
            (chat_id, title, ts, ts),
        )

    def list_chats(self) -> list[ChatRow]:
        rows = self._conn.execute("SELECT * FROM seen_chats ORDER BY last_seen DESC").fetchall()
        return [_row_to(ChatRow, row) for row in rows]

    def get_message(self, row_id: int) -> MessageRow | None:
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (row_id,)).fetchone()
        return None if row is None else _row_to(MessageRow, row)

    def max_message_id(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS top FROM messages").fetchone()
        return int(row["top"])

    def client_messages_after(
        self, after_id: int, chat_ids: Collection[int], staff_ids: Collection[int]
    ) -> list[MessageRow]:
        """Сообщения из клиентских чатов после указанного id, без сообщений сотрудников."""
        if not chat_ids:
            return []
        sql = (
            "SELECT * FROM messages WHERE id > ? AND chat_id IN ("
            + ", ".join("?" * len(chat_ids))
            + ")"
        )
        params: list[Any] = [after_id, *sorted(chat_ids)]
        if staff_ids:
            sql += (
                " AND (author_id IS NULL OR author_id NOT IN ("
                + ", ".join("?" * len(staff_ids))
                + "))"
            )
            params.extend(sorted(staff_ids))
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to(MessageRow, row) for row in rows]

    def chat_messages(self, chat_id: int, limit: int) -> list[MessageRow]:
        rows = self._conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id",
            (chat_id, limit),
        ).fetchall()
        return [_row_to(MessageRow, row) for row in rows]

    def pending_transcriptions(self) -> list[MessageRow]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE status = 'transcribing' ORDER BY id"
        ).fetchall()
        return [_row_to(MessageRow, row) for row in rows]

    def set_transcription(self, row_id: int, *, text: str, status: str, attempts: int) -> None:
        self._conn.execute(
            "UPDATE messages SET text = ?, status = ?, attempts = ? WHERE id = ?",
            (text, status, attempts, row_id),
        )

    # исходящие сообщения

    def get_outbox(self, key: str) -> OutboxRow | None:
        row = self._conn.execute("SELECT * FROM outbox WHERE key = ?", (key,)).fetchone()
        return None if row is None else _row_to(OutboxRow, row)

    def insert_outbox_pending(
        self,
        *,
        key: str,
        chat_id: int,
        text: str,
        reply_to_msg_id: int | None,
        file_path: str | None,
        created_at: int,
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO outbox "
            "(key, chat_id, text, reply_to_msg_id, file_path, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (key, chat_id, text, reply_to_msg_id, file_path, created_at),
        )

    def claim_outbox(self, key: str) -> bool:
        """Переводит запись в sending. Возвращает False, если её уже взял другой процесс."""
        cursor = self._conn.execute(
            "UPDATE outbox SET status = 'sending', error = NULL "
            "WHERE key = ? AND status IN ('pending', 'failed')",
            (key,),
        )
        return cursor.rowcount == 1

    def finish_outbox(
        self,
        key: str,
        *,
        status: str,
        tg_message_ids: Sequence[int] = (),
        error: str | None = None,
        sent_at: int | None = None,
    ) -> None:
        joined = ",".join(str(item) for item in tg_message_ids) or None
        self._conn.execute(
            "UPDATE outbox SET status = ?, tg_message_id = COALESCE(?, tg_message_id), "
            "error = ?, sent_at = COALESCE(?, sent_at) WHERE key = ?",
            (status, joined, error, sent_at, key),
        )

    def outbox_for_chat(self, chat_id: int, limit: int) -> list[OutboxRow]:
        rows = self._conn.execute(
            "SELECT * FROM (SELECT * FROM outbox WHERE chat_id = ? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id",
            (chat_id, limit),
        ).fetchall()
        return [_row_to(OutboxRow, row) for row in rows]

    def last_sent_per_chat(self) -> list[OutboxRow]:
        """Последнее успешно отправленное сообщение в каждом чате."""
        rows = self._conn.execute(
            "SELECT * FROM outbox WHERE id IN "
            "(SELECT MAX(id) FROM outbox WHERE status = 'sent' GROUP BY chat_id)"
        ).fetchall()
        return [_row_to(OutboxRow, row) for row in rows]
