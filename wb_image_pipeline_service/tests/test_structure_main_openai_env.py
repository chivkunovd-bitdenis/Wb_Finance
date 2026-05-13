"""Резолв ключа и base URL для structure_main (fallback на переменные монолита)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_openai_key_prefers_wip_over_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIP_OPENAI_API_KEY", "wip-secret")
    monkeypatch.setenv("AI_API_KEY", "ai-secret")

    from app.services import structure_main_openai as m

    assert m._openai_api_key() == "wip-secret"


def test_openai_key_falls_back_to_ai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIP_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_API_KEY", "from-monolith")

    from app.services import structure_main_openai as m

    assert m._openai_api_key() == "from-monolith"


def test_openai_base_falls_back_to_ai_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIP_OPENAI_API_BASE_URL", raising=False)
    monkeypatch.setenv("AI_API_BASE_URL", "https://example.com/v1")

    from app.services import structure_main_openai as m

    assert m._chat_completions_url() == "https://example.com/v1/chat/completions"


def test_call_structure_posts_to_resolved_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIP_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setenv("AI_API_BASE_URL", "https://api.openai.com/v1")

    envelope = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "seo_title": "T",
                            "seo_description": "word " * 30,
                            "main_prompts": ["a", "b", "c", "d"],
                        }
                    )
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

    import app.services.structure_main_openai as smo

    with patch.object(smo.httpx, "Client", return_value=mock_cm):
        out = smo.call_structure_main_model(user_prompt="hello")

    assert out.seo_title == "T"
    mock_client.post.assert_called_once()
    url = mock_client.post.call_args[0][0]
    assert url == "https://api.openai.com/v1/chat/completions"
