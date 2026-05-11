from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from app.services.ai_wb_access_service import user_storage_state_path


app = FastAPI(title="WB Auth Manager", version="1.0.0")
logger = logging.getLogger(__name__)


def _require_internal_token(token: str | None) -> None:
    expected = (os.getenv("WB_AUTH_INTERNAL_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WB auth manager is not configured")
    if (token or "").strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


class StartRequest(BaseModel):
    user_id: str


class SaveRequest(BaseModel):
    user_id: str


@dataclass
class _Session:
    playwright: Any
    browser: Any
    context: Any
    page: Any


_lock = asyncio.Lock()
_sessions: dict[str, _Session] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/start")
async def start(
    req: StartRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, str]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    # Robustness: always recreate the session. Users can open new tabs or navigate away,
    # and Playwright may end up controlling a non-visible page. Recreate guarantees a single
    # visible window on WB login root.
    async with _lock:
        existing = _sessions.pop(user_id, None)

    if existing is not None:
        with contextlib.suppress(Exception):
            await existing.context.close()
        with contextlib.suppress(Exception):
            await existing.browser.close()
        with contextlib.suppress(Exception):
            await existing.playwright.stop()

    # Lazy import to keep startup light.
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    # Must run headed on virtual display (Xvfb provides DISPLAY inside container).
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://seller.wildberries.ru/", wait_until="domcontentloaded", timeout=60_000)
    try:
        logger.info("wb_auth start: user_id=%s url=%s", user_id, page.url)
    except Exception:
        pass

    async with _lock:
        _sessions[user_id] = _Session(playwright=p, browser=browser, context=context, page=page)
    return {"status": "ok"}


@app.post("/save")
async def save(
    req: SaveRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, str]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    async with _lock:
        sess = _sessions.get(user_id)
        if sess is None:
            raise HTTPException(status_code=409, detail="session_not_started")

    out = user_storage_state_path(user_id=user_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    await sess.context.storage_state(path=str(out))

    # Close session (best-effort)
    with contextlib.suppress(Exception):
        await sess.context.close()
    with contextlib.suppress(Exception):
        await sess.browser.close()
    with contextlib.suppress(Exception):
        await sess.playwright.stop()

    async with _lock:
        _sessions.pop(user_id, None)

    # Ensure file exists and is not empty.
    if not out.is_file() or out.stat().st_size < 50:
        raise HTTPException(status_code=500, detail="failed_to_save_storage_state")

    # Lock down perms best-effort (works on linux).
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass

    return {"status": "ok", "path": str(out)}

