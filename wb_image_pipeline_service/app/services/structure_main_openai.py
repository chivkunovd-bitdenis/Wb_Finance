"""PG-B.2: вызов OpenAI chat.completions для структуризации (JSON)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.schemas.structure_main import StructureMainResult
from app.services.openai_chat_vision import image_part, text_part
from app.services.reference_fetch_client import ReferenceImage
from app.services.wip_openai_httpx import openai_httpx_client

logger = logging.getLogger(__name__)

_STRUCTURE_SYSTEM = """Ты арт-директор и prompt-planner для генерации главных фото карточки Wildberries.

Тебе переданы:
1. исходные референсы товара;
2. текст пользователя с пожеланиями к генерации.

Сначала внимательно изучи исходные референсы товара. Используй изображения как главный источник правды о товаре.

Определи:
- что это за товар;
- категорию товара;
- ключевые видимые особенности;
- материал, фактуру, цвет, форму, крой или конструкцию;
- что обязательно сохранить, чтобы товар остался тем же;
- какие ракурсы, крупности, композиции и стилистические решения лучше подходят именно этому товару.

Затем проанализируй текст пользователя. Пожелания пользователя нужно учесть при создании 4 промптов, но не копировать механически. Органично встрои смысл пожеланий в промпты так, чтобы они выглядели как профессиональные задания на съёмку товара.

Если пожелания пользователя противоречат исходным референсам, приоритет у референсов. Нельзя менять категорию, цвет, материал, форму, фасон, конструкцию и ключевые детали товара.

Не используй универсальный список деталей. Не упоминай конкретные элементы товара, если их нет на референсе или они не важны.

Верни ТОЛЬКО JSON без markdown и пояснений.

Ключи:
- "seo_title" — короткое название товара для карточки (строка, до 200 символов);
- "seo_description" — описание для карточки (строка, 1–3 абзаца);
- "main_prompts" — ровно 4 строки: каждая — отдельный текстовый промпт для генерации главного фото Wildberries по исходным референсам. Пиши на русском.

Требования к main_prompts:
- ровно 4 промпта;
- каждый промпт описывает отдельное главное фото Wildberries по исходным референсам;
- все 4 промпта сохраняют тот же товар: категорию, цвет, материал, фактуру, форму, пропорции и ключевые детали;
- пожелания пользователя должны быть органично встроены в каждый промпт там, где они уместны;
- 4 промпта должны быть разными по подаче: ракурс, крупность, поза или положение товара, композиция, фон, свет;
- не делай 4 одинаковых средних фронтальных кадра;
- если товар на модели, между 4 вариантами можно менять модель, фон, позу, свет и стиль, но нельзя менять сам товар;
- если товар предметный, между 4 вариантами можно менять сетап, фон, композицию и свет, но нельзя менять сам товар;
- не добавляй текст, логотипы, лишние надписи, случайные аксессуары и несуществующие детали;
- каждый промпт должен быть самодостаточным для генерации изображения вместе с исходными референсами.

Все строки непустые."""


def _openai_api_key() -> str | None:
    """Сначала ключ сервиса WIP, иначе тот же `AI_API_KEY`, что и у монолита (daily_brief / offer_rag / отзывы)."""
    for env_name in ("WIP_OPENAI_API_KEY", "AI_API_KEY"):
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            return raw
    return None


def _openai_api_base() -> str:
    """База API: WIP-специфичная или общая с монолитом (`AI_API_BASE_URL`)."""
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


def call_structure_main_model(*, user_prompt: str, reference_images: list[ReferenceImage]) -> StructureMainResult:
    """
    Вызывает OpenAI и возвращает провалидированный результат.

    Raises:
        ValueError: нет ключа API, пустой промпт, невалидный ответ.
        OSError, TimeoutError: сетевые сбои при запросе к API.
    """
    key = _openai_api_key()
    if not key:
        raise ValueError("Set WIP_OPENAI_API_KEY or reuse monolith AI_API_KEY")
    text = (user_prompt or "").strip()
    if not text:
        raise ValueError("user_prompt is empty")
    if not reference_images:
        raise ValueError("reference images are required for structure_main")

    model = _structure_model()
    url = _chat_completions_url()
    user_content: list[dict[str, Any]] = [
        text_part(
            "\n".join(
                [
                    "Текст пользователя с пожеланиями к генерации:",
                    text,
                    "",
                    "Сгенерируй SEO-поля и 4 промпта для главных фото по переданным исходным референсам.",
                ]
            )
        ),
        *[image_part(ref) for ref in reference_images[:16]],
    ]
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _STRUCTURE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    with openai_httpx_client(timeout=_timeout_sec()) as client:
        r = client.post(url, json=body, headers=headers)

    if r.status_code != 200:
        logger.warning(
            "wip_structure_openai: status=%s body=%s",
            r.status_code,
            r.text[:800],
        )
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

    out = StructureMainResult.model_validate(parsed)
    logger.info(
        "wip_structure_openai: ok model=%s prompts=%s title_len=%s",
        model,
        len(out.main_prompts),
        len(out.seo_title),
    )
    return out
