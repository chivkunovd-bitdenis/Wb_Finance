"""PG-C.1: GPT JSON-промпты для 7 дополнительных фото карточки WB."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.schemas.content_series import ContentSeriesResult
from app.services.wip_openai_httpx import openai_httpx_client

logger = logging.getLogger(__name__)

_CONTENT_SERIES_SYSTEM = """Ты арт-директор карточек Wildberries. Верни ТОЛЬКО JSON-объект без markdown и пояснений.
Ключи:
- "series_prompts" — ровно 7 строк: промпты для генерации дополнительных фото карточки WB по выбранному фото.

Правила для series_prompts:
- строго сохраняй тот же товар на модели: одежда, цвет, материал, фактура, крой, пропорции и конструктив как на референсном кадре;
- палитра и «семейство» локации должны ощущаться одной карточкой, но допускается смещение плана фона, глубины резкости и ракурса так, чтобы кадры не выглядели дубликатами;
- серия как на WB: нет смены категории, бренда, фасона или ткани;
- поза и камера: семь кадров — семь различимых комбинаций (крупность + ракурс + жест). Запрещено: семь средних планов анфас с одной и той же стойкой;
- инфографика: ровно 3 строки из 7 (выбери индексы 1, 4 и 6 в массиве, считая с 0) должны явно требовать аккуратные иконки и короткие подписи на русском (1–3 слова), только из фактов входного описания; в остальных 4 строках явно укажи «без текста и без иконок на изображении»;
- в кадрах без инфографики сильнее варьируй ракурс (низкий, высокий, сзади с поворотом головы, крупный план ткани) и действие модели;
- лицо может быть частично вне кадра или в профиль, если это помогает разнообразию — комплект одежды остаётся читаемым;
- инфографика: без обещаний, которых нет во входном описании; без мелкого текста абзацами;
- каждый промпт самодостаточен для image edit по выбранному фото.

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


def call_content_series_model(*, selected_prompt: str, product_context: str | None) -> ContentSeriesResult:
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
    context = (product_context or "").strip()
    if not prompt:
        raise ValueError("selected image prompt is empty")

    user_text = "\n".join(
        [
            "Выбранный пользователем кадр/промпт:",
            prompt,
            "",
            "Контекст товара от продавца и пайплайна:",
            context or "Контекст не указан; опирайся на выбранное фото и промпт.",
            "",
            "Сделай 7 промптов для наполнения карточки WB.",
        ]
    )
    model = _structure_model()
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _CONTENT_SERIES_SYSTEM},
            {"role": "user", "content": user_text},
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
