"""Fetch product reference images from the monolith for reference-based image generation."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReferenceImage:
    asset_id: str
    filename: str
    mime_type: str
    content: bytes
    sha256_hex: str


class ReferenceFetchError(ValueError):
    """Raised when WIP cannot load a required monolith reference image."""


def _reference_secret() -> str:
    return (settings.monolith_reference_secret or settings.internal_hmac_secret or "").strip()


def _reference_fetch_timeout_sec() -> float:
    return 30.0


def _filename_from_content_disposition(value: str, *, asset_id: str) -> str:
    marker = "filename="
    if marker in value:
        raw = value.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
        if raw:
            return raw
    return f"{asset_id}.png"


def fetch_reference_images(
    *,
    monolith_job_id: str,
    reference_asset_ids: list[str],
    max_images: int = 16,
) -> list[ReferenceImage]:
    """Download reference files from monolith by asset id. At least one image is required."""
    job_id = str(monolith_job_id or "").strip()
    asset_ids = [str(a).strip() for a in reference_asset_ids if str(a).strip()]
    if not job_id:
        raise ReferenceFetchError("monolith_job_id is required for reference fetch")
    if not asset_ids:
        raise ReferenceFetchError("reference_asset_ids is empty")

    base = (settings.monolith_base_url or "").strip().rstrip("/")
    secret = _reference_secret()
    if not base:
        raise ReferenceFetchError("WIP_MONOLITH_BASE_URL is not configured")
    if not secret:
        raise ReferenceFetchError("WIP_MONOLITH_REFERENCE_SECRET is not configured")

    out: list[ReferenceImage] = []
    headers = {"Authorization": f"Bearer {secret}"}
    with httpx.Client(timeout=_reference_fetch_timeout_sec(), trust_env=False) as client:
        for asset_id in asset_ids[:max_images]:
            url = f"{base}/ai/product-generation/internal/jobs/{job_id}/references/{asset_id}/file"
            try:
                res = client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning("wip_reference_fetch: HTTP error job=%s asset=%s: %s", job_id, asset_id, exc)
                raise ReferenceFetchError(f"reference fetch failed for {asset_id}: {exc}") from exc
            if res.status_code == 404:
                raise ReferenceFetchError(f"reference asset not found: {asset_id}")
            if res.status_code != 200:
                logger.warning(
                    "wip_reference_fetch: status=%s job=%s asset=%s body=%s",
                    res.status_code,
                    job_id,
                    asset_id,
                    res.text[:500],
                )
                raise ReferenceFetchError(f"reference fetch HTTP {res.status_code} for {asset_id}")
            raw = res.content
            if not raw:
                raise ReferenceFetchError(f"reference asset is empty: {asset_id}")
            mime = (res.headers.get("content-type") or "application/octet-stream").split(";", 1)[0].strip()
            filename = _filename_from_content_disposition(
                res.headers.get("content-disposition") or "",
                asset_id=asset_id,
            )
            out.append(
                ReferenceImage(
                    asset_id=asset_id,
                    filename=filename,
                    mime_type=mime or "application/octet-stream",
                    content=raw,
                    sha256_hex=hashlib.sha256(raw).hexdigest(),
                )
            )

    if not out:
        raise ReferenceFetchError("no reference images fetched")
    logger.info("wip_reference_fetch: fetched refs=%s job=%s", len(out), job_id)
    return out


def reference_metadata(refs: list[ReferenceImage]) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": r.asset_id,
            "filename": r.filename,
            "mime_type": r.mime_type,
            "sha256_hex": r.sha256_hex,
        }
        for r in refs
    ]
