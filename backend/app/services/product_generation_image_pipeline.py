"""PG-3.4: HTTP-клиент к wb_image_pipeline_service (internal runs API).

Поток **IMAGE** (PG-A.1): для старта run монолиту достаточно ≥1 загруженного референса
(даёт `reference_asset_ids`) и статуса `draft` → `POST .../start`. Поля карточки товара
(`title`, `vendor_code`, `brand`, габариты, `price_kopeks`, `sizes_json`, …) **не обязательны**
на этом этапе и уходят в payload как `null`/опущенные значения — заполнение карточки
относится к потоку **PRODUCT/WB** (PATCH после фото).
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any

import httpx

from app.models.product_generation_job import ProductGenerationJob
from app.schemas.product_generation import ProductGenerationJobOut

logger = logging.getLogger(__name__)

_STEP_TITLE_RU: dict[str, str] = {
    "structure_main": "Структура карточки (OpenAI JSON: SEO, промпты к картинкам)",
    "images_main": "Генерация изображений (OpenAI / сохранение ассетов)",
    "pg32_stub": "Финализация run (сервисный шаг)",
}

_RUN_STATUS_HINT_RU: dict[str, str] = {
    "created": "Run создан; дальше Celery ставит цепочку `run_created` → `structure_main` → `images_main` → `step_done`.",
    "queued": "Run в очереди воркера.",
    "running": "Воркер выполняет шаги пайплайна.",
    "in_progress": "Воркер выполняет шаги пайплайна.",
    "completed": "Удалённый пайплайн завершён успешно.",
    "failed": "Пайплайн остановился с ошибкой — смотрите шаги ниже.",
    "cancelled": "Run отменён.",
}

_STEP_STATUS_RU: dict[str, str] = {
    "pending": "ожидает запуска",
    "running": "выполняется",
    "done": "завершён успешно",
    "failed": "ошибка",
    "skipped": "пропущен",
}


def _step_waiting_hint(step_key: str, status: str) -> str | None:
    sk = step_key.lower()
    st = status.lower()
    if st not in ("running", "pending"):
        return None
    if sk == "structure_main":
        return "Обычно здесь ждём ответ OpenAI (chat/completions, JSON)."
    if sk == "images_main":
        return "Обычно здесь ждём ответ OpenAI по изображениям и запись файлов в ассеты."
    if sk == "pg32_stub":
        return "Короткий финальный шаг перед сменой статуса run."
    return None


def _timeline_entry(*, time_iso: str, level: str, title: str, body: str) -> dict[str, str]:
    return {
        "time": time_iso,
        "level": level,
        "title": title,
        "body": body,
    }


def _ordinal_of(step: dict[str, Any]) -> int:
    raw = step.get("ordinal")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return 999


def build_image_pipeline_timeline(remote: dict[str, Any]) -> list[dict[str, str]]:
    """Человекочитаемая хронология по снимку GET /internal/v1/runs/{id} (для UI «Лог»)."""
    entries: list[dict[str, str]] = []
    rid = str(remote.get("id") or "").strip() or "—"
    rstatus = str(remote.get("status") or "").strip() or "—"
    hint = _RUN_STATUS_HINT_RU.get(rstatus.lower(), f"Статус run: {rstatus}.")
    mono = str(remote.get("monolith_job_id") or "").strip()
    updated = str(remote.get("updated_at") or "").strip()
    created = str(remote.get("created_at") or "").strip()
    time0 = updated or created
    head_lines = [
        f"Run ID (wb_image_pipeline_service): {rid}",
        f"Статус run: {rstatus}",
        hint,
    ]
    if mono:
        head_lines.append(f"Связанная задача монолита (monolith_job_id): {mono}")
    if created:
        head_lines.append(f"Создано (WIP): {created}")
    if updated:
        head_lines.append(f"Обновлено (WIP): {updated}")
    head_lines.append(
        "Монолит при каждом опросе делает GET к WIP и строит этот снимок; "
        "если шаг «в работе», ответ OpenAI может ещё не попасть в БД.",
    )
    lvl = "error" if rstatus.lower() == "failed" else "info"
    entries.append(_timeline_entry(time_iso=time0, level=lvl, title="Удалённый image-run (wb_image_pipeline_service)", body="\n".join(head_lines)))

    steps_raw = remote.get("steps") or []
    if not isinstance(steps_raw, list):
        return entries

    parsed: list[tuple[int, dict[str, Any]]] = []
    for s in steps_raw:
        if isinstance(s, dict):
            parsed.append((_ordinal_of(s), s))
    parsed.sort(key=lambda x: (x[0], str(x[1].get("step_key") or "")))

    for _ord, s in parsed:
        key = str(s.get("step_key") or "").strip() or "шаг"
        st = str(s.get("status") or "").strip() or "—"
        title = _STEP_TITLE_RU.get(key, f"Шаг «{key}»")
        st_ru = _STEP_STATUS_RU.get(st.lower(), st)
        su = str(s.get("updated_at") or "").strip()
        sc = str(s.get("created_at") or "").strip()
        err = s.get("error_message")
        err_s = str(err).strip() if err is not None else ""
        body_lines = [f"Статус шага: {st_ru} ({st})"]
        if sc:
            body_lines.append(f"Создано шага: {sc}")
        if su:
            body_lines.append(f"Обновлено шага: {su}")
        wait = _step_waiting_hint(key, st)
        if wait:
            body_lines.append(wait)
        if err_s:
            body_lines.append(f"Сообщение об ошибке (WIP): {err_s[:1900]}")
        elif st.lower() in ("running", "pending"):
            body_lines.append("Ошибок по шагу в снимке пока нет — идёт ожидание или запись результата.")
        step_lvl = "error" if st.lower() == "failed" else "info"
        t_iso = su or sc or time0
        entries.append(_timeline_entry(time_iso=t_iso, level=step_lvl, title=title, body="\n".join(body_lines)))

    return entries


class ImagePipelineClientError(Exception):
    """Ошибка вызова image-сервиса (сеть, 4xx/5xx, неверное тело)."""


def image_pipeline_base_url() -> str | None:
    raw = (os.getenv("PRODUCT_GEN_IMAGE_PIPELINE_BASE_URL") or "").strip().rstrip("/")
    return raw or None


def image_pipeline_secret() -> str | None:
    raw = (os.getenv("PRODUCT_GEN_IMAGE_PIPELINE_SECRET") or "").strip()
    return raw or None


def is_image_pipeline_enabled() -> bool:
    return bool(image_pipeline_base_url() and image_pipeline_secret())


def _timeout_sec() -> float:
    raw = (os.getenv("PRODUCT_GEN_IMAGE_PIPELINE_TIMEOUT_SEC") or "30").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 30.0


def _json_safe_decimal(val: Decimal | None) -> str | None:
    if val is None:
        return None
    return str(val)


def build_image_pipeline_payload(job: ProductGenerationJob) -> dict[str, Any]:
    """Собирает JSON `payload` для `POST /internal/v1/runs` (фаза IMAGE, без требований к карточке)."""
    refs = list(job.reference_paths_json or [])
    asset_ids: list[str] = []
    for r in refs:
        if isinstance(r, dict) and r.get("asset_id"):
            asset_ids.append(str(r["asset_id"]))
    return {
        "reference_asset_ids": asset_ids,
        "title": job.title,
        "vendor_code": job.vendor_code,
        "brand": job.brand,
        "wb_subject_id": job.wb_subject_id,
        "description_user": job.description_user,
        "seo_description": job.seo_description,
        "price_kopeks": job.price_kopeks,
        "dimensions_length": _json_safe_decimal(job.dimensions_length),
        "dimensions_width": _json_safe_decimal(job.dimensions_width),
        "dimensions_height": _json_safe_decimal(job.dimensions_height),
        "weight_brutto": _json_safe_decimal(job.weight_brutto),
        "sizes_json": job.sizes_json,
    }


def create_remote_run(monolith_job_id: str, payload: dict[str, Any]) -> str:
    base = image_pipeline_base_url()
    secret = image_pipeline_secret()
    if not base or not secret:
        raise ImagePipelineClientError("image pipeline env not configured")
    url = f"{base}/internal/v1/runs"
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
    body = {"monolith_job_id": monolith_job_id, "payload": payload}
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=_timeout_sec(), trust_env=False)
    except httpx.HTTPError as exc:
        logger.warning("product_generation: image pipeline POST failed: %s", exc)
        raise ImagePipelineClientError(str(exc)) from exc
    if r.status_code not in (200, 201):
        logger.warning(
            "product_generation: image pipeline POST status=%s body=%s",
            r.status_code,
            r.text[:500],
        )
        raise ImagePipelineClientError(f"unexpected status {r.status_code}")
    try:
        data = r.json()
    except ValueError as exc:
        raise ImagePipelineClientError("invalid JSON response") from exc
    run_id = data.get("id")
    if not run_id or not isinstance(run_id, str):
        raise ImagePipelineClientError("missing run id in response")
    return run_id


def fetch_remote_run(run_id: str) -> dict[str, Any] | None:
    base = image_pipeline_base_url()
    secret = image_pipeline_secret()
    if not base or not secret:
        return None
    url = f"{base}/internal/v1/runs/{run_id}"
    headers = {"Authorization": f"Bearer {secret}"}
    try:
        r = httpx.get(url, headers=headers, timeout=_timeout_sec(), trust_env=False)
    except httpx.HTTPError as exc:
        logger.warning("product_generation: image pipeline GET failed run_id=%s: %s", run_id, exc)
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        logger.warning(
            "product_generation: image pipeline GET status=%s run_id=%s",
            r.status_code,
            run_id,
        )
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _is_remote_pipeline_run_id(run_id: str | None) -> bool:
    if not run_id:
        return False
    return not str(run_id).startswith("local-")


def enrich_job_out_with_image_pipeline(out: ProductGenerationJobOut) -> ProductGenerationJobOut:
    """Добавляет снимок статуса image-run для поллинга UI (только при включённом клиенте)."""
    if not is_image_pipeline_enabled():
        return out.model_copy(update={"image_pipeline": None})
    rid = out.pipeline_run_id
    if not _is_remote_pipeline_run_id(rid):
        return out.model_copy(update={"image_pipeline": None})
    remote = fetch_remote_run(str(rid))
    if remote is None:
        return out.model_copy(update={"image_pipeline": None})
    steps = remote.get("steps") or []
    compact_steps: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for s in steps:
            if isinstance(s, dict):
                err = s.get("error_message")
                err_s = str(err).strip()[:2000] if err is not None else None
                ca = s.get("created_at")
                ua = s.get("updated_at")
                row: dict[str, Any] = {
                    "step_key": s.get("step_key"),
                    "status": s.get("status"),
                    "ordinal": s.get("ordinal"),
                    "error_message": err_s or None,
                }
                if ca is not None:
                    row["created_at"] = ca
                if ua is not None:
                    row["updated_at"] = ua
                compact_steps.append(row)
    last_error: str | None = None
    for s in compact_steps:
        if str(s.get("status") or "") == "failed" and s.get("error_message"):
            last_error = str(s["error_message"])[:900]
            break
    snapshot: dict[str, Any] = {
        "remote_status": remote.get("status"),
        "updated_at": remote.get("updated_at"),
        "created_at": remote.get("created_at"),
        "monolith_job_id": remote.get("monolith_job_id"),
        "steps": compact_steps,
        "last_error": last_error,
        "timeline": build_image_pipeline_timeline(remote),
    }
    return out.model_copy(update={"image_pipeline": snapshot})
