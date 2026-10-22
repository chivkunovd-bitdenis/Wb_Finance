"""Клиент Telegram Bot API поверх httpx.

Токен входит в адрес каждого запроса. Поэтому клиент не печатает URL, а ошибки API
приходят как TelegramError с кодом и описанием, без адреса.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

API_ROOT = "https://api.telegram.org"
LONG_POLL_SEC = 25
_DEFAULT_TIMEOUT = 40.0
_TRANSFER_TIMEOUT = 120.0


class TelegramError(Exception):
    """Telegram ответил ошибкой. code - HTTP-код или error_code из ответа."""

    def __init__(self, code: int | None, description: str) -> None:
        self.code = code
        self.description = description
        super().__init__(f"Telegram вернул ошибку {code}: {description}")


class TelegramClient:
    def __init__(self, token: str, *, http: httpx.Client | None = None) -> None:
        self._api = f"{API_ROOT}/bot{token}"
        self._files = f"{API_ROOT}/file/bot{token}"
        self._http = http if http is not None else httpx.Client(timeout=_DEFAULT_TIMEOUT)

    def close(self) -> None:
        self._http.close()

    def _call(
        self,
        method: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        response = self._http.post(
            f"{self._api}/{method}",
            json=json_body,
            data=data,
            files=files,
            timeout=timeout,
        )
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(response.status_code, "ответ не в формате JSON") from None
        if not isinstance(body, dict):
            raise TelegramError(response.status_code, "неожиданный формат ответа")
        if not body.get("ok"):
            code = body.get("error_code")
            raise TelegramError(
                code if isinstance(code, int) else response.status_code,
                str(body.get("description", "без описания")),
            )
        return body.get("result")

    def get_me(self) -> dict[str, Any]:
        return dict(self._call("getMe"))

    def get_updates(
        self, *, offset: int, timeout: int, allowed_updates: list[str]
    ) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates",
            json_body={"offset": offset, "timeout": timeout, "allowed_updates": allowed_updates},
            timeout=timeout + 15,
        )
        return list(result or [])

    def send_message(
        self, *, chat_id: int, text: str, reply_to: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to is not None:
            body["reply_to_message_id"] = reply_to
        return dict(self._call("sendMessage", json_body=body))

    def send_document(
        self, *, chat_id: int, path: Path, caption: str | None, reply_to: int | None
    ) -> dict[str, Any]:
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        if reply_to is not None:
            data["reply_to_message_id"] = str(reply_to)
        content = path.read_bytes()
        return dict(
            self._call(
                "sendDocument",
                data=data,
                files={"document": (path.name, content)},
                timeout=_TRANSFER_TIMEOUT,
            )
        )

    def get_file(self, file_id: str) -> dict[str, Any]:
        return dict(self._call("getFile", json_body={"file_id": file_id}))

    def download_file(self, file_path: str) -> bytes:
        response = self._http.get(f"{self._files}/{file_path}", timeout=_TRANSFER_TIMEOUT)
        if response.status_code != 200:
            raise TelegramError(response.status_code, "не удалось скачать файл")
        return response.content
