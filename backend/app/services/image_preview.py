"""Уменьшенные превью сгенерированных фото для галереи (оригиналы по 1.5–2 МБ PNG)."""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

PREVIEW_MAX_SIDE = 900
PREVIEW_JPEG_QUALITY = 82


def make_preview_jpeg(content: bytes, *, max_side: int = PREVIEW_MAX_SIDE) -> bytes | None:
    """PNG/JPEG/WebP → JPEG с длинной стороной ≤ max_side. None, если картинку не удалось прочитать."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=PREVIEW_JPEG_QUALITY, optimize=True, progressive=True)
            return out.getvalue()
    except Exception:
        logger.exception("image_preview: failed to build preview")
        return None
