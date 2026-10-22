"""Расшифровка голосовых через OpenAI (аудио-эндпоинт transcriptions, язык русский)."""

from __future__ import annotations

import httpx

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
_TIMEOUT = 120.0


class TranscriptionError(Exception):
    """Расшифровка не удалась. Текст ошибки не содержит ключа: в запросе он только в заголовке."""


class OpenAITranscriber:
    def __init__(self, api_key: str, model: str, *, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._http = http if http is not None else httpx.Client(timeout=_TIMEOUT)

    def transcribe(self, audio: bytes, filename: str) -> str:
        response = self._http.post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            data={"model": self._model, "language": "ru"},
            files={"file": (filename, audio, "audio/ogg")},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            raise TranscriptionError(f"OpenAI вернул HTTP {response.status_code}")
        try:
            return str(response.json()["text"]).strip()
        except (ValueError, KeyError, TypeError):
            raise TranscriptionError("неожиданный ответ OpenAI") from None
