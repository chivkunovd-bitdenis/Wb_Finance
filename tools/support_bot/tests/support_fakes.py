"""Фейки и фабрики для тестов. Ни одного сетевого вызова: ни Telegram, ни OpenAI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from wbf_support_bot.config import Config, parse_config
from wbf_support_bot.telegram import TelegramError

TOKEN = "123456789:AAFAKE_token_for_tests_only_0123"
OPENAI_KEY = "sk-fake-openai-key-for-tests-9999"
OWNER_ID = 1000001
STAFF_ID = 1000002
CLIENT_USER_ID = 2000001
CLIENT_CHAT = -1001111111111
OTHER_CHAT = -1002222222222
BASE_TS = 1_800_000_000


def make_config_dict(state_dir: Path, *, openai_key: str = "") -> dict[str, Any]:
    return {
        "state_dir": str(state_dir),
        "bot_token": TOKEN,
        "owner_user_id": OWNER_ID,
        "internal_user_ids": [STAFF_ID],
        "chats": {str(CLIENT_CHAT): {"client": "Ромашка"}},
        "openai_api_key": openai_key,
        "transcribe_model": "gpt-4o-mini-transcribe",
    }


def make_config(state_dir: Path, *, openai_key: str = "") -> Config:
    return parse_config(make_config_dict(state_dir, openai_key=openai_key))


def _user(user_id: int = CLIENT_USER_ID) -> dict[str, Any]:
    return {
        "id": user_id,
        "is_bot": False,
        "first_name": "Ольга",
        "last_name": "Смирнова",
        "username": "olga",
    }


def _chat(chat_id: int = CLIENT_CHAT, chat_type: str = "supergroup") -> dict[str, Any]:
    return {"id": chat_id, "type": chat_type, "title": "Чат клиента Ромашка"}


def _base_message(
    msg_id: int,
    *,
    chat_id: int,
    author_id: int,
    date: int,
    chat_type: str = "supergroup",
) -> dict[str, Any]:
    return {
        "message_id": msg_id,
        "date": date,
        "chat": _chat(chat_id, chat_type),
        "from": _user(author_id),
    }


def text_update(
    update_id: int,
    msg_id: int,
    *,
    text: str = "Здравствуйте, где отчёт?",
    chat_id: int = CLIENT_CHAT,
    author_id: int = CLIENT_USER_ID,
    reply_to: int | None = None,
    date: int = BASE_TS,
    chat_type: str = "supergroup",
) -> dict[str, Any]:
    message = _base_message(
        msg_id, chat_id=chat_id, author_id=author_id, date=date, chat_type=chat_type
    )
    message["text"] = text
    if reply_to is not None:
        message["reply_to_message"] = {
            "message_id": reply_to,
            "date": date,
            "chat": _chat(chat_id),
            "text": "Наш предыдущий ответ",
        }
    return {"update_id": update_id, "message": message}


def edited_update(
    update_id: int,
    msg_id: int,
    text: str,
    *,
    chat_id: int = CLIENT_CHAT,
    author_id: int = CLIENT_USER_ID,
) -> dict[str, Any]:
    message = _base_message(msg_id, chat_id=chat_id, author_id=author_id, date=BASE_TS)
    message["text"] = text
    message["edit_date"] = BASE_TS + 60
    return {"update_id": update_id, "edited_message": message}


def voice_message(
    msg_id: int,
    *,
    chat_id: int = CLIENT_CHAT,
    author_id: int = CLIENT_USER_ID,
    file_id: str = "VOICE_FILE_1",
) -> dict[str, Any]:
    message = _base_message(msg_id, chat_id=chat_id, author_id=author_id, date=BASE_TS)
    message["voice"] = {"file_id": file_id, "duration": 4}
    return message


def voice_update(update_id: int, msg_id: int, **kwargs: Any) -> dict[str, Any]:
    return {"update_id": update_id, "message": voice_message(msg_id, **kwargs)}


def photo_update(update_id: int, msg_id: int, caption: str | None = None) -> dict[str, Any]:
    message = _base_message(msg_id, chat_id=CLIENT_CHAT, author_id=CLIENT_USER_ID, date=BASE_TS)
    message["photo"] = [{"file_id": "PHOTO_SMALL"}, {"file_id": "PHOTO_BIG"}]
    if caption:
        message["caption"] = caption
    return {"update_id": update_id, "message": message}


def document_update(update_id: int, msg_id: int, file_name: str) -> dict[str, Any]:
    message = _base_message(msg_id, chat_id=CLIENT_CHAT, author_id=CLIENT_USER_ID, date=BASE_TS)
    message["document"] = {"file_id": "DOC_1", "file_name": file_name}
    message["caption"] = "Вот акт"
    return {"update_id": update_id, "message": message}


def sticker_update(update_id: int, msg_id: int) -> dict[str, Any]:
    message = _base_message(msg_id, chat_id=CLIENT_CHAT, author_id=CLIENT_USER_ID, date=BASE_TS)
    message["sticker"] = {"file_id": "STICKER_1", "emoji": "🙂"}
    return {"update_id": update_id, "message": message}


class FakeTelegram:
    """Подменяет Telegram: отдаёт заготовленные обновления и запоминает отправленное."""

    def __init__(self) -> None:
        self.update_batches: list[list[dict[str, Any]]] = []
        self.get_updates_calls: list[dict[str, Any]] = []
        self.get_updates_error: BaseException | None = None
        self.sent: list[dict[str, Any]] = []
        self.send_failures: list[BaseException] = []
        self.lose_response = False
        self.file_requests: list[str] = []
        self.files: dict[str, bytes] = {"VOICE_FILE_1": b"OggS-fake-voice"}
        self._next_id = 500

    def get_me(self) -> dict[str, Any]:
        return {"id": 999, "is_bot": True, "first_name": "WB Finance", "username": "wbf_test_bot"}

    def get_updates(
        self, *, offset: int, timeout: int, allowed_updates: list[str]
    ) -> list[dict[str, Any]]:
        self.get_updates_calls.append(
            {"offset": offset, "timeout": timeout, "allowed_updates": list(allowed_updates)}
        )
        if self.get_updates_error is not None:
            raise self.get_updates_error
        if self.update_batches:
            return self.update_batches.pop(0)
        return []

    def _next_message_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _check_failure(self) -> None:
        if self.send_failures:
            raise self.send_failures.pop(0)

    def send_message(
        self, *, chat_id: int, text: str, reply_to: int | None = None
    ) -> dict[str, Any]:
        self._check_failure()
        message_id = self._next_message_id()
        self.sent.append(
            {
                "kind": "message",
                "chat_id": chat_id,
                "text": text,
                "reply_to": reply_to,
                "message_id": message_id,
            }
        )
        if self.lose_response:
            # Сообщение ушло, но ответ потерян по сети: отправитель не знает, дошло ли.
            raise httpx.ReadTimeout("read timed out")
        return {"message_id": message_id, "chat": {"id": chat_id}}

    def send_document(
        self, *, chat_id: int, path: Path, caption: str | None, reply_to: int | None
    ) -> dict[str, Any]:
        self._check_failure()
        message_id = self._next_message_id()
        self.sent.append(
            {
                "kind": "document",
                "chat_id": chat_id,
                "file": path.name,
                "caption": caption,
                "reply_to": reply_to,
                "message_id": message_id,
            }
        )
        return {"message_id": message_id, "chat": {"id": chat_id}}

    def get_file(self, file_id: str) -> dict[str, Any]:
        self.file_requests.append(file_id)
        return {"file_id": file_id, "file_path": f"voice/{file_id}.oga"}

    def download_file(self, file_path: str) -> bytes:
        stem = Path(file_path).stem
        if stem not in self.files:
            raise TelegramError(404, "файл не найден")
        return self.files[stem]


class FakeTranscriber:
    """Подменяет OpenAI. Каждый элемент results - либо текст, либо исключение."""

    def __init__(self, results: list[str | BaseException] | None = None) -> None:
        self.results: list[str | BaseException] = list(results or [])
        self.calls: list[tuple[bytes, str]] = []

    def transcribe(self, audio: bytes, filename: str) -> str:
        self.calls.append((audio, filename))
        if not self.results:
            return "расшифровка по умолчанию"
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    """Часы для тестов: sleep() только двигает время и вызывает on_sleep."""

    def __init__(self, start: int = BASE_TS) -> None:
        self.now = float(start)
        self.sleeps: list[float] = []
        self.on_sleep: Any = None

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()
