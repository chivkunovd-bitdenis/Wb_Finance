"""Helpers for sending local reference images to OpenAI chat/completions."""

from __future__ import annotations

import base64
from typing import Any

from app.services.reference_fetch_client import ReferenceImage


def text_part(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def image_part(ref: ReferenceImage) -> dict[str, Any]:
    mime_type = (ref.mime_type or "image/png").strip() or "image/png"
    encoded = base64.b64encode(ref.content).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }
