from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.types import ASGIApp

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


def _get_client_ip(request: Request) -> str | None:
    # Prefer proxy-provided IP; keep first hop only.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return None


def _get_user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    ua = ua.strip()
    return ua[:300] if ua else None


def _ensure_request_id(request: Request) -> str:
    existing = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
    rid = existing if existing else uuid.uuid4().hex
    request.state.request_id = rid
    return rid


class RequestLoggingMiddleware:
    """
    Lightweight request log with correlation id.

    Never logs Authorization header or request body.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        rid = _ensure_request_id(request)
        method = (request.method or "").upper()
        path = request.url.path if request.url else scope.get("path")
        ip = _get_client_ip(request)
        ua = _get_user_agent(request)

        start = time.monotonic()
        status_code: int | None = None

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = message.get("headers") or []
                headers.append((REQUEST_ID_HEADER.lower().encode(), rid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            user_id = getattr(request.state, "user_id", None)
            auth_error = getattr(request.state, "auth_error", None)
            # status_code can be None if app crashed before response start.
            code = int(status_code or 500)
            logger.info(
                "request rid=%s method=%s path=%s status=%s ms=%.1f ip=%s user_id=%s auth_error=%s ua=%s",
                rid,
                method,
                path,
                code,
                elapsed_ms,
                ip,
                user_id,
                auth_error,
                ua,
            )


def set_request_id_header(response: Response, request: Request) -> Response:
    """
    Helper for edge cases when middleware isn't installed.
    """

    rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    response.headers[REQUEST_ID_HEADER] = rid
    return response

