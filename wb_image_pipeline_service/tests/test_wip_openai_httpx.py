"""Тесты: исходящий httpx к OpenAI не подхватывает мёртвый системный прокси."""

from __future__ import annotations

from unittest.mock import patch

import httpx


def test_openai_httpx_client_uses_trust_env_false() -> None:
    with patch.object(httpx, "Client") as m_client:
        from app.services.wip_openai_httpx import openai_httpx_client

        openai_httpx_client(timeout=30.0)
        m_client.assert_called_once()
        kwargs = m_client.call_args.kwargs
        assert kwargs.get("trust_env") is False
        assert "proxy" not in kwargs


def test_openai_httpx_client_passes_explicit_proxy(monkeypatch) -> None:
    monkeypatch.setenv("WIP_HTTPS_PROXY", "http://proxy.example:8888")
    with patch.object(httpx, "Client") as m_client:
        from app.services.wip_openai_httpx import openai_httpx_client

        openai_httpx_client(timeout=15.0)
        kwargs = m_client.call_args.kwargs
        assert kwargs.get("trust_env") is False
        assert kwargs.get("proxy") == "http://proxy.example:8888"
