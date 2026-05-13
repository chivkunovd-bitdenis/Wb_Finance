"""PG-B.3: разбор ответа OpenAI images/generations (без реального API)."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest


from app.services.reference_fetch_client import ReferenceImage


def _ref() -> ReferenceImage:

    return ReferenceImage(
        asset_id="ref-1",
        filename="ref.png",
        mime_type="image/png",
        content=b"ref-bytes",
        sha256_hex="ref-sha",
    )


def test_call_openai_image_bytes_from_b64_json() -> None:
    import app.services.images_main_openai as m

    raw = b"\x89PNG\r\n\x1a\nfake"
    b64 = base64.b64encode(raw).decode("ascii")

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"data": [{"b64_json": b64}]}

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = fake_resp

    with patch.object(m, "_openai_api_key", return_value="sk-test"), patch(
        "httpx.Client", return_value=fake_client
    ):
        out, mime = m.call_openai_image_bytes(prompt="hello", reference_images=[_ref()])

    assert out == raw
    assert mime == "image/png"
    fake_client.post.assert_called_once()
    url = fake_client.post.call_args[0][0]
    assert url.endswith("/images/edits")
    kwargs = fake_client.post.call_args.kwargs
    assert kwargs["data"]["prompt"] == "hello"
    assert kwargs["data"]["n"] == 1
    assert kwargs["files"][0][0] == "image[]"


def test_call_openai_image_bytes_http_error() -> None:
    import app.services.images_main_openai as m

    fake_resp = MagicMock()
    fake_resp.status_code = 429
    fake_resp.text = "rate"

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = fake_resp

    with patch.object(m, "_openai_api_key", return_value="sk-test"), patch(
        "httpx.Client", return_value=fake_client
    ):
        with pytest.raises(ValueError, match="429"):
            m.call_openai_image_bytes(prompt="x", reference_images=[_ref()])


def test_call_openai_image_bytes_invalid_json_content() -> None:
    import app.services.images_main_openai as m

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.side_effect = json.JSONDecodeError("x", "", 0)

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = fake_resp

    with patch.object(m, "_openai_api_key", return_value="sk-test"), patch(
        "httpx.Client", return_value=fake_client
    ):
        with pytest.raises(ValueError, match="not JSON"):
            m.call_openai_image_bytes(prompt="x", reference_images=[_ref()])


def test_call_openai_image_bytes_requires_reference() -> None:
    import app.services.images_main_openai as m

    with patch.object(m, "_openai_api_key", return_value="sk-test"):
        with pytest.raises(ValueError, match="reference image is required"):
            m.call_openai_image_bytes(prompt="x", reference_images=[])
