"""PG-C.1: GPT JSON-промпты для 7 дополнительных фото карточки WB."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.schemas.content_series import ContentSeriesResult
from app.services.openai_chat_vision import image_part, text_part
from app.services.reference_fetch_client import ReferenceImage
from app.services.wip_openai_httpx import openai_httpx_client

logger = logging.getLogger(__name__)

_CONTENT_SERIES_SYSTEM = """Ты арт-директор карточек Wildberries. Верни ТОЛЬКО JSON-объект без markdown и пояснений.
Ключи:
- "series_prompts" — ровно 7 строк: промпты для генерации дополнительных фото карточки WB по выбранному фото.

Тебе переданы:
1. выбранное главное фото;
2. prompt выбранного главного фото.

Выбранное главное фото — главный источник правды. Prompt выбранного главного фото используй только как контекст.

Перед созданием series_prompts внутренне проанализируй выбранное фото:
- какой это товар;
- какие видимые особенности товара важны;
- какие ракурсы, крупности и фокусы подходят именно этому товару;
- какие детали товара коммерчески важно показать крупно.

Не возвращай этот анализ отдельно. Используй его только для выбора 7 кадров.

Жёсткий принцип:
7 фото должны выглядеть как кадры из одной и той же съёмки с той же моделью, в той же локации, в том же луке и с тем же товаром. Это не новая фотосессия и не набор разных изображений.

Менять можно только:
- ракурс камеры;
- крупность кадра;
- позу модели или положение товара;
- кадрирование;
- сторону показа товара;
- фокус на конкретной детали товара;
- аккуратный графический слой, только если он уместен.

Не используй универсальный список деталей. Выбирай детали по конкретному товару на выбранном фото. Не упоминай элементы, которых нет на товаре или которые не важны.

Требования к series_prompts:
- ровно 7 промптов;
- каждый промпт описывает один кадр из той же самой фотосессии;
- в каждом промпте коротко закрепи: та же модель, та же локация, тот же лук, тот же товар;
- кадры должны отличаться только ракурсом, крупностью, позой, кадрированием и фокусом на деталях товара;
- выбери ракурсы и детали под конкретный товар на изображении, а не по универсальному списку;
- в серии должны быть разные полезные кадры: общий вид товара, альтернативные стороны или ракурсы, крупный план материала/фактуры и крупный план важных деталей, если они реально есть;
- если для товара уместна инфографика, сделай 1–2 кадра с аккуратным графическим слоем поверх кадра из этой же фотосессии;
- инфографика должна использовать только факты, видимые на выбранном фото;
- если фактов для инфографики нет, не добавляй текст и иконки;
- не добавляй несуществующие свойства, обещания, логотипы, случайные надписи и новые детали;
- каждый промпт должен быть самодостаточным для генерации изображения вместе с выбранным главным фото.

Все строки непустые."""


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


def _chat_completions_url() -> str:
    return f"{_openai_api_base()}/chat/completions"


def _structure_model() -> str:
    return (os.getenv("WIP_OPENAI_MODEL_STRUCTURE") or "gpt-4.1-mini").strip()


def _timeout_sec() -> float:
    raw = (os.getenv("WIP_OPENAI_TIMEOUT_SEC") or "120").strip()
    try:
        return max(15.0, float(raw))
    except ValueError:
        return 120.0


def call_content_series_model(*, selected_prompt: str, selected_reference_image: ReferenceImage) -> ContentSeriesResult:
    """
    Возвращает 7 промптов для дополнительных фото по выбранному главному кадру.

    Raises:
        ValueError: нет ключа API, пустой контекст, невалидный ответ.
        OSError, TimeoutError: сетевые сбои при запросе к API.
    """
    key = _openai_api_key()
    if not key:
        raise ValueError("Set WIP_OPENAI_API_KEY or reuse monolith AI_API_KEY")
    prompt = (selected_prompt or "").strip()
    if not prompt:
        raise ValueError("selected image prompt is empty")

    user_content: list[dict[str, Any]] = [
        text_part(
            "\n".join(
                [
                    "Prompt выбранного главного фото:",
                    prompt,
                    "",
                    "Пользовательский текст и первичные референсы не передаются. Сделай 7 промптов для той же фотосессии по выбранному главному фото.",
                ]
            )
        ),
        image_part(selected_reference_image),
    ]
    model = _structure_model()
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _CONTENT_SERIES_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    with openai_httpx_client(timeout=_timeout_sec()) as client:
        r = client.post(_chat_completions_url(), json=body, headers=headers)

    if r.status_code != 200:
        logger.warning("wip_content_series_openai: status=%s body=%s", r.status_code, r.text[:800])
        raise ValueError(f"OpenAI HTTP {r.status_code}")

    try:
        envelope = r.json()
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI response is not JSON") from exc

    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenAI response missing choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenAI empty content")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("OpenAI JSON root must be an object")

    out = ContentSeriesResult.model_validate(parsed)
    logger.info("wip_content_series_openai: ok model=%s prompts=%s", model, len(out.series_prompts))
    return out
