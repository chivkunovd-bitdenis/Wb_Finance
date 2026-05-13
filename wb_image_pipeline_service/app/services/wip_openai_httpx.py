"""Настройки исходящего httpx для вызовов OpenAI из WIP.

В Docker часто пробрасывают ``HTTP(S)_PROXY`` из ``backend/.env`` для выхода в OpenAI.
Пока прокси на хосте выключен, httpx с ``trust_env=True`` (дефолт) пытается
коннектиться к ``host.docker.internal:7890`` и падает с ``[Errno 111] Connection refused``.

Для вызовов к ``api.openai.com`` отключаем доверие к переменным окружения прокси.
Если прокси нужен **только** для OpenAI, задайте ``WIP_HTTPS_PROXY`` / ``WIP_HTTP_PROXY``
— они передаются в клиент явно.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


def openai_httpx_client(*, timeout: float) -> httpx.Client:
    proxy = (os.getenv("WIP_HTTPS_PROXY") or os.getenv("WIP_HTTP_PROXY") or "").strip() or None
    kwargs: dict[str, Any] = {"timeout": timeout, "trust_env": False}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)
