from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _fake_reference() -> Any:
    from app.services.reference_fetch_client import ReferenceImage

    return ReferenceImage(
        asset_id="main-1",
        filename="main.png",
        mime_type="image/png",
        content=b"selected-main",
        sha256_hex="sha",
    )


def test_call_content_series_posts_selected_image_without_user_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIP_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_API_KEY", "k")

    envelope = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"series_prompts": [f"p{i}" for i in range(7)]})
                }
            }
        ]
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = envelope

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False

    import app.services.content_series_openai as cso

    with patch.object(cso, "openai_httpx_client", return_value=mock_cm):
        out = cso.call_content_series_model(
            selected_prompt="та же фотосессия, крупный план фактуры",
            selected_reference_image=_fake_reference(),
        )

    assert len(out.series_prompts) == 7
    body = mock_client.post.call_args.kwargs["json"]
    user_content = body["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1]["type"] == "image_url"
    assert "Пользовательский текст" in user_content[0]["text"]
    assert "description_user" not in user_content[0]["text"]
