"""
PG-B.1: зашитый шаблон промпта для фазы IMAGE + склейка с `description_user` из монолита.

Не использует title/vendor_code/sizes — только пользовательский текст (может быть пустым).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Версия логики шаблона (не путать с hash тела шаблона).
PROMPT_TEMPLATE_VERSION = "pg-b1-v1"

_DEFAULT_TEMPLATE = """Ты готовишь промпт для генерации главных фото карточки товара Wildberries.

Цель: 4 разных главных фото одного и того же товара с референса: высокое качество, максимальная детализация, реализм, аккуратная модельная внешность, современный Pinterest/e-commerce vibe.

Жёсткий инвариант:
- генерировать только предмет, который изображён на референсе;
- не менять категорию товара, крой, тип изделия, ключевые детали, материал и цвет;
- если текст продавца уточняет товар, считать это уточнение более приоритетным, чем общий стиль шаблона;
- стиль, модель, локация, свет, фон и ракурс могут отличаться, но сам товар должен оставаться тем же.

Ниже — текст продавца о товаре. Используй его как приоритетное уточнение предмета, который нужно сохранить с референса.

--- Пожелания продавца ---
{user_text}
--- Конец пожеланий ---"""


def _user_text_max_chars() -> int:
    raw = (os.getenv("WIP_IMAGE_PROMPT_USER_TEXT_MAX_CHARS") or "8000").strip()
    try:
        return max(1, min(32000, int(raw)))
    except ValueError:
        return 8000


def active_prompt_template() -> str:
    """Полный текст шаблона; переопределяется env `WIP_IMAGE_PROMPT_TEMPLATE` (должен содержать `{user_text}`)."""
    raw = (os.getenv("WIP_IMAGE_PROMPT_TEMPLATE") or "").strip()
    if raw:
        if "{user_text}" not in raw:
            logger.warning(
                "wip_image_prompt: WIP_IMAGE_PROMPT_TEMPLATE set but missing {user_text}; using default"
            )
            return _DEFAULT_TEMPLATE
        return raw
    return _DEFAULT_TEMPLATE


def template_body_fingerprint(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]


def normalize_user_text(description_user: str | None) -> tuple[str, bool, bool]:
    """
    Возвращает (фрагмент для вставки в шаблон, was_empty, was_truncated).
    Пустой ввод заменяется на нейтральную заглушку (PG-B.1: монолит может не требовать текст на старте).
    """
    s = (description_user or "").strip()
    if not s:
        return "(описание не задано)", True, False
    limit = _user_text_max_chars()
    if len(s) > limit:
        logger.info(
            "wip_image_prompt: description_user truncated run_len=%s max=%s",
            len(s),
            limit,
        )
        return s[:limit], False, True
    return s, False, False


def build_effective_image_prompt(monolith_payload: dict[str, Any]) -> str:
    """Склеивает шаблон и пользовательский текст (без зависимости от полей карточки)."""
    template = active_prompt_template()
    user_part, was_empty, was_truncated = normalize_user_text(
        monolith_payload.get("description_user") if isinstance(monolith_payload, dict) else None
    )
    if "{user_text}" not in template:
        raise ValueError("prompt_template_missing_placeholder")
    merged = template.replace("{user_text}", user_part)
    if was_empty:
        logger.info("wip_image_prompt: empty description_user after strip, using placeholder")
    if was_truncated:
        logger.info("wip_image_prompt: user text was truncated before merge")
    fp = template_body_fingerprint(template)
    logger.info(
        "wip_image_prompt: baked effective prompt chars=%s template_fp=%s version=%s",
        len(merged),
        fp,
        PROMPT_TEMPLATE_VERSION,
    )
    return merged


def bake_prompt_fields(monolith_payload: dict[str, Any]) -> dict[str, Any]:
    """Поля для merge в `wip_runs.payload_json` после валидации HTTP."""
    template = active_prompt_template()
    effective = build_effective_image_prompt(monolith_payload)
    return {
        "wip_effective_image_prompt": effective,
        "wip_prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "wip_prompt_template_hash": template_body_fingerprint(template),
    }
