"""PG-B.3a: вызов OpenAI images/edits для одного кадра по референсу."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from app.services.reference_fetch_client import ReferenceImage
from app.services.wip_openai_httpx import openai_httpx_client

logger = logging.getLogger(__name__)


def _openai_api_key() -> str | None:
    for env_name in ("WIP_OPENAI_API_KEY", "AI_API_KEY"):
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            return raw
    return None


def _openai_api_base() -> str:
    for env_name in ("WIP_OPENAI_API_BASE_URL", "AI_API_BASE_URL"):
        raw = (os.getenv(env_name) or "").strip().rstrip("/")
        if raw:
            return raw
    return "https://api.openai.com/v1"


def _images_edits_url() -> str:
    return f"{_openai_api_base()}/images/edits"


def _image_model() -> str:
    return (os.getenv("WIP_OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()


def _timeout_sec() -> float:
    raw = (os.getenv("WIP_OPENAI_TIMEOUT_SEC") or "120").strip()
    try:
        return max(15.0, float(raw))
    except ValueError:
        return 120.0


def call_openai_image_bytes(*, prompt: str, reference_images: list[ReferenceImage]) -> tuple[bytes, str]:
    """
    Генерирует одно изображение по текстовому промпту и обязательным reference image(s).

    Returns:
        Сырые байты файла и MIME-тип (например ``image/png``).

    Raises:
        ValueError: нет ключа, пустой промпт, ошибка HTTP или формата ответа.
        OSError, TimeoutError: сетевые сбои при запросе к API.
    """
    key = _openai_api_key()
    if not key:
        raise ValueError("Set WIP_OPENAI_API_KEY or reuse monolith AI_API_KEY")
    text = (prompt or "").strip()
    if not text:
        raise ValueError("image prompt is empty")
    if not reference_images:
        raise ValueError("reference image is required")

    model = _image_model()
    url = _images_edits_url()
    data: dict[str, Any] = {
        "model": model,
        "prompt": text,
        "n": 1,
        "size": "1024x1024",
        "output_format": "png",
    }
    files = [
        ("image[]", (ref.filename, ref.content, ref.mime_type))
        for ref in reference_images[:16]
    ]
    headers = {"Authorization": f"Bearer {key}"}

    with openai_httpx_client(timeout=_timeout_sec()) as client:
        r = client.post(url, data=data, files=files, headers=headers)

    if r.status_code != 200:
        logger.warning(
            "wip_images_openai_edit: status=%s body=%s",
            r.status_code,
            r.text[:800],
        )
        raise ValueError(f"OpenAI image edit HTTP {r.status_code}")

    try:
        envelope = r.json()
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI image edit response is not JSON") from exc

    data = envelope.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("OpenAI image edit response missing data")
    first = data[0]
    if not isinstance(first, dict):
        raise ValueError("OpenAI image edit data[0] invalid")

    b64 = first.get("b64_json")
    if isinstance(b64, str) and b64.strip():
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception as exc:
            raise ValueError("OpenAI image b64_json decode failed") from exc
        if not raw:
            raise ValueError("OpenAI image empty after decode")
        logger.info("wip_images_openai_edit: ok model=%s refs=%s bytes=%s", model, len(reference_images), len(raw))
        return raw, "image/png"

    url_field = first.get("url")
    if isinstance(url_field, str) and url_field.strip():
        with openai_httpx_client(timeout=_timeout_sec()) as client:
            gr = client.get(url_field)
        if gr.status_code != 200:
            raise ValueError(f"OpenAI image URL fetch HTTP {gr.status_code}")
        raw = gr.content
        if not raw:
            raise ValueError("OpenAI image URL empty body")
        ct = gr.headers.get("content-type", "image/png")
        mime = ct.split(";")[0].strip() if ct else "image/png"
        logger.info("wip_images_openai_edit: ok via url model=%s refs=%s bytes=%s", model, len(reference_images), len(raw))
        return raw, mime

    raise ValueError("OpenAI image edit response missing b64_json and url")
