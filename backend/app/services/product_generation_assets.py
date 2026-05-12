from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES_DEFAULT = 15 * 1024 * 1024
_MAX_FILES_DEFAULT = 20

_ALLOWED_CT_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def references_dir_root() -> Path:
    raw = (os.getenv("PRODUCT_GENERATION_REFERENCES_DIR") or "").strip()
    base = Path(raw) if raw else Path("/app/data/product_generation_references")
    return base.resolve()


def _max_file_bytes() -> int:
    raw = (os.getenv("PRODUCT_GENERATION_REFERENCE_MAX_BYTES") or "").strip()
    if not raw:
        return _MAX_FILE_BYTES_DEFAULT
    try:
        return max(1, int(raw))
    except ValueError:
        return _MAX_FILE_BYTES_DEFAULT


def _max_files() -> int:
    raw = (os.getenv("PRODUCT_GENERATION_REFERENCE_MAX_FILES") or "").strip()
    if not raw:
        return _MAX_FILES_DEFAULT
    try:
        return max(1, min(100, int(raw)))
    except ValueError:
        return _MAX_FILES_DEFAULT


def _sanitize_original_filename(name: str) -> str:
    base = os.path.basename((name or "").strip()) or "image"
    cleaned = "".join(c for c in base if c.isprintable() and c not in '<>:"/\\|?*\x00')
    return (cleaned[:200] or "image").strip() or "image"


def _pick_extension(*, content_type: str | None, original_filename: str) -> str | None:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in _ALLOWED_CT_TO_EXT:
        return _ALLOWED_CT_TO_EXT[ct]
    lower = original_filename.lower()
    for ext in _ALLOWED_CT_TO_EXT.values():
        if lower.endswith(ext):
            return ext
    return None


async def save_reference_uploads(
    *,
    user_id: str,
    job_id: str,
    uploads: list[UploadFile],
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Пишет файлы на диск, возвращает записи для reference_paths_json и пути (для отката)."""
    if not uploads:
        raise ValueError("no_files")
    max_n = _max_files()
    if len(uploads) > max_n:
        raise ValueError("too_many_files")

    root = references_dir_root()
    job_dir = root / str(user_id) / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = _max_file_bytes()
    written: list[Path] = []
    records: list[dict[str, Any]] = []

    try:
        for upload in uploads:
            orig = _sanitize_original_filename(upload.filename or "")
            ct_raw = (upload.content_type or "").split(";", 1)[0].strip().lower() or None
            ext = _pick_extension(content_type=ct_raw, original_filename=orig)
            if ext is None:
                raise ValueError("bad_content_type")

            asset_id = uuid.uuid4().hex
            stored_name = f"{asset_id}{ext}"
            dest = job_dir / stored_name

            size = 0
            with dest.open("wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("file_too_large")
                    out.write(chunk)

            written.append(dest)
            records.append(
                {
                    "asset_id": asset_id,
                    "original_filename": orig,
                    "content_type": ct_raw or "application/octet-stream",
                    "size_bytes": size,
                    "stored_name": stored_name,
                }
            )
            logger.info(
                "product_generation: saved reference asset_id=%s job=%s user=%s bytes=%s",
                asset_id,
                job_id,
                user_id,
                size,
            )
    except Exception:
        for p in written:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                logger.warning("product_generation: failed to unlink partial %s", p)
        raise

    return records, written


def resolve_reference_path(*, user_id: str, job_id: str, stored_name: str) -> Path | None:
    """Возвращает абсолютный путь к файлу, если имя безопасно (только basename, совпадает с ожидаемым)."""
    safe = Path(stored_name).name
    if safe != stored_name or not safe or ".." in stored_name:
        return None
    root = references_dir_root()
    base = (root / str(user_id) / str(job_id)).resolve()
    path = (base / safe).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path
