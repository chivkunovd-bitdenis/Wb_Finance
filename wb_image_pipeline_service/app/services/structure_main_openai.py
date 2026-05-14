"""PG-B.2: вызов OpenAI chat.completions для структуризации (JSON)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.schemas.structure_main import StructureMainResult
from app.services.wip_openai_httpx import openai_httpx_client

logger = logging.getLogger(__name__)

_STRUCTURE_SYSTEM = """Ты помощник для маркетплейса. По входному тексту продавца верни ТОЛЬКО один JSON-объект без markdown и без пояснений.
Ключи:
- "seo_title" — короткое название товара для карточки (строка, до 200 символов);
- "seo_description" — описание для карточки (строка, 1–3 абзаца);
- "main_prompts" — ровно 4 строки: каждая — отдельный текстовый промпт для генерации изображения главного вида товара по референсу. Пиши на русском.

Правила для main_prompts:
- все 4 промпта описывают один и тот же товар с референса и текста продавца;
- нельзя менять категорию товара (поло не превращается в обувь и т.д.);
- каждый промпт явно требует сохранить крой, цвет, материал, фактуру, конструктив и пропорции изделия с референса;
- четыре строки должны сильно отличаться друг от друга. Запрещено: четыре вариации «модель стоит анфас, камера на уровне глаз, средний план, размытый фон»;
- распределение по строкам (строго по порядку индексов 0..3 в массиве):
  * [0] — студийный или минималистичный фон, чётко обозначенная крупность (например поясной или 3/4), нейтральная поза допустима только здесь;
  * [1] — другая локация (lifestyle: улица, кафе, лобби, лестница), другой свет и другая поза (шаг, поворот корпуса, сидя);
  * [2] — другой ракурс камеры (снизу вверх ИЛИ сверху вниз ИЛИ сбоку ~45°), поза с жестом (руки не висят симметрично вдоль тела);
  * [3] — крупный план деталей товара на модели (ворот, планка, текстура ткани, застёжки) ИЛИ полный рост с необычной композицией (асимметрия кадра, направляющие линии), но товар целиком узнаваем;
- модель, возрастная группа, телосложение и причёска могут меняться между промптами, если это не противоречит тексту продавца;
- в каждом промпте одной фразой запрети смену фасона/бренда и «галлюцинированные» надписи на реквизите.

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


def call_structure_main_model(*, user_prompt: str) -> StructureMainResult:
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

    model = _structure_model()
    url = _chat_completions_url()
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _STRUCTURE_SYSTEM},
            {"role": "user", "content": text},
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
