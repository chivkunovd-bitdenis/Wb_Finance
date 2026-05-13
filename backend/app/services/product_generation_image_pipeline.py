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
        r = httpx.post(url, json=body, headers=headers, timeout=_timeout_sec())
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
        r = httpx.get(url, headers=headers, timeout=_timeout_sec())
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
                compact_steps.append(
                    {
                        "step_key": s.get("step_key"),
                        "status": s.get("status"),
                        "ordinal": s.get("ordinal"),
                        "error_message": err_s or None,
                    }
                )
    last_error: str | None = None
    for s in compact_steps:
        if str(s.get("status") or "") == "failed" and s.get("error_message"):
            last_error = str(s["error_message"])[:900]
            break
    snapshot: dict[str, Any] = {
        "remote_status": remote.get("status"),
        "updated_at": remote.get("updated_at"),
        "steps": compact_steps,
        "last_error": last_error,
    }
    return out.model_copy(update={"image_pipeline": snapshot})
